from flask import Flask, render_template, request, redirect, url_for, flash, session
from dotenv import load_dotenv
import PyPDF2
import pytesseract
from pdf2image import convert_from_path
from werkzeug.utils import secure_filename
import tempfile
import shutil
import uuid
import os
import psycopg2
from tax_calculator import compare_regimes
import re
import requests
import json

# Load environment variables
load_dotenv()

app = Flask(__name__, template_folder='templates')

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB limit

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def call_gemini_for_extraction(raw_text):
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        return None
    url = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=' + api_key
    prompt = (
        "Extract the following fields from this salary slip text: "
        "gross_salary, basic_salary, hra_received, rent_paid, deduction_80c, deduction_80d, standard_deduction, professional_tax, tds. "
        "Text: " + raw_text + "\n"
        "Return the result as a JSON object with those keys."
    )
    headers = {"Content-Type": "application/json"}
    data = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    try:
        print('Gemini prompt:', prompt)
        resp = requests.post(url, headers=headers, data=json.dumps(data), timeout=30)
        resp.raise_for_status()
        result = resp.json()
        print('Gemini raw response:', json.dumps(result, indent=2))
        # Gemini returns the text in candidates[0]['content']['parts'][0]['text']
        text = result['candidates'][0]['content']['parts'][0]['text']
        # Try to extract JSON from the response
        json_start = text.find('{')
        json_end = text.rfind('}') + 1
        if json_start != -1 and json_end != -1:
            parsed = json.loads(text[json_start:json_end])
            return parsed
    except Exception as e:
        print('Gemini extraction error:', e)
    return None

def extract_pdf_data(filepath):
    # Try text extraction with PyPDF2
    try:
        with open(filepath, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            text = " ".join(page.extract_text() or '' for page in reader.pages)
    except Exception:
        text = ''
    # If text is empty, try OCR
    if not text.strip():
        try:
            images = convert_from_path(filepath)
            text = ''
            for img in images:
                text += pytesseract.image_to_string(img)
        except Exception:
            text = ''
    # Try Gemini extraction
    gemini_data = call_gemini_for_extraction(text)
    if gemini_data:
        return gemini_data
    # Fallback: use regex extraction as before
    def find(pattern, default=None, cast=float):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return cast(match.group(1).replace(',', '').replace('₹', '').strip())
            except Exception:
                return default
        return default
    hra = (
        find(r'HRA Received[:\s]+([\d,]+)') or
        find(r'House Rent Allowance[:\s]+([\d,]+)') or
        find(r'HRA[:\s]+([\d,]+)') or
        find(r'HRA.*?(\d[\d,]*)')
    )
    data = {
        'gross_salary': find(r'Gross Salary[:\s]+([\d,]+)', None),
        'basic_salary': find(r'Basic(?: Salary)?[:\s]+([\d,]+)', None),
        'hra_received': hra,
        'rent_paid': find(r'Rent Paid[:\s]+([\d,]+)', None),
        'deduction_80c': find(r'80C(?: Deduction)?[:\s]+([\d,]+)', None),
        'deduction_80d': find(r'80D(?: Deduction)?[:\s]+([\d,]+)', None),
        'standard_deduction': find(r'Standard Deduction[:\s]+([\d,]+)', 50000),
        'professional_tax': find(r'Professional Tax[:\s]+([\d,]+)', None),
        'tds': find(r'TDS[:\s]+([\d,]+)', None),
    }
    return data

# Helper to call Gemini API for Q&A
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GEMINI_API_URL = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=' + GEMINI_API_KEY

def gemini_ask(prompt):
    headers = {'Content-Type': 'application/json'}
    data = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    try:
        response = requests.post(GEMINI_API_URL, headers=headers, data=json.dumps(data))
        response.raise_for_status()
        result = response.json()
        return result['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print(f"Gemini API error: {e}")
        return "Sorry, I couldn't get a response from Gemini."

def log_ai_conversation(session_id, entry):
    log_file = 'ai_conversation_log.json'
    try:
        if os.path.exists(log_file):
            with open(log_file, 'r', encoding='utf-8') as f:
                log = json.load(f)
        else:
            log = {}
        if session_id not in log:
            log[session_id] = []
        log[session_id].append(entry)
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(log, f, indent=2)
    except Exception as e:
        print(f"AI log error: {e}")

@app.route('/advisor', methods=['GET', 'POST'])
def advisor():
    session_id = request.args.get('session_id') or request.form.get('session_id')
    if not session_id:
        return "Session ID missing.", 400
    if request.method == 'GET':
        # Generate a follow-up question based on user data
        # (In a real app, fetch user data from DB. Here, just a sample prompt.)
        prompt = f"Given the user's tax data for session {session_id}, ask a smart, relevant follow-up question to help optimize their taxes."
        question = gemini_ask(prompt)
        log_ai_conversation(session_id, {"role": "gemini", "type": "question", "text": question})
        return render_template('ask.html', question=question, session_id=session_id, answer=None, suggestions=None)
    else:
        # POST: user answered the question
        answer = request.form.get('answer')
        question = request.form.get('question')
        log_ai_conversation(session_id, {"role": "user", "type": "answer", "text": answer})
        # Now get Gemini's personalized suggestions
        prompt = f"User's answer: {answer}\nBased on all previous data and this answer, provide personalized, actionable investment and tax-saving suggestions in a modern, readable card format."
        suggestions = gemini_ask(prompt)
        log_ai_conversation(session_id, {"role": "gemini", "type": "suggestions", "text": suggestions})
        return render_template('ask.html', question=question, session_id=session_id, answer=answer, suggestions=suggestions)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        if 'pdf' not in request.files:
            flash('No file part')
            return redirect(request.url)
        file = request.files['pdf']
        if file.filename == '':
            flash('No selected file')
            return redirect(request.url)
        if file and allowed_file(file.filename):
            filename = secure_filename(f"{uuid.uuid4()}_{file.filename}")
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            extracted = extract_pdf_data(filepath)
            # Pass extracted fields to review
            return redirect(url_for('review', **{k: v for k, v in extracted.items() if v is not None}))
        else:
            flash('Invalid file type. Only PDF allowed.')
            return redirect(request.url)
    return '''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Upload Pay Slip or Form 16</title>
        <link href="https://fonts.googleapis.com/css2?family=Aptos+Display:wght@400;700&display=swap" rel="stylesheet">
        <style>
            body { font-family: 'Aptos Display', Arial, sans-serif; background: #f8fbff; color: #222; margin: 0; }
            .container { max-width: 480px; margin: 6vh auto; background: #fff; border-radius: 18px; box-shadow: 0 4px 24px rgba(0,80,200,0.07); padding: 2.5rem 2rem 2rem 2rem; text-align: center; }
            h1 { color: #1565c0; font-size: 2rem; margin-bottom: 0.7rem; font-weight: 700; }
            p { color: #444; font-size: 1.08rem; margin-bottom: 2.2rem; }
            .file-input { display: block; margin: 1.2rem auto 2rem auto; font-size: 1.1rem; }
            .upload-btn { background: #1976d2; color: #fff; font-size: 1.15rem; font-weight: 600; border: none; border-radius: 8px; padding: 0.8rem 2.1rem; cursor: pointer; transition: background 0.2s; }
            .upload-btn:hover, .upload-btn:focus { background: #0d47a1; outline: none; }
            @media (max-width: 600px) { .container { padding: 1rem; margin-top: 2vh; } h1 { font-size: 1.5rem; } }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Upload Pay Slip or Form 16</h1>
            <p>Select your PDF file. We’ll extract your salary and tax data automatically. Only PDF files are accepted.</p>
            <form method="post" enctype="multipart/form-data">
                <input class="file-input" type="file" name="pdf" accept="application/pdf" required>
                <button class="upload-btn" type="submit">Upload & Continue</button>
            </form>
        </div>
    </body>
    </html>
    '''

@app.route('/review', methods=['GET', 'POST'])
def review():
    if request.method == 'POST':
        reviewed_data = request.form.to_dict()
        # Ensure all required fields are present and default to 0 if missing
        for key in [
            'gross_salary', 'basic_salary', 'hra_received', 'rent_paid',
            'deduction_80c', 'deduction_80d', 'standard_deduction',
            'professional_tax', 'tds']:
            if reviewed_data.get(key) in [None, '']:
                reviewed_data[key] = 0
        session_id = str(uuid.uuid4())
        reviewed_data['session_id'] = session_id
        selected_regime = reviewed_data.get('selected_regime', 'new')
        print('Reviewed data:', reviewed_data)
        comparison = compare_regimes(reviewed_data, selected_regime)
        print('Comparison:', comparison)
        db_url = os.getenv('DB_URL')
        try:
            conn = psycopg2.connect(db_url)
            cur = conn.cursor()
            print('Inserting UserFinancials:', (
                session_id,
                reviewed_data.get('gross_salary'),
                reviewed_data.get('basic_salary'),
                reviewed_data.get('hra_received'),
                reviewed_data.get('rent_paid'),
                reviewed_data.get('deduction_80c'),
                reviewed_data.get('deduction_80d'),
                reviewed_data.get('standard_deduction', 50000),
                reviewed_data.get('professional_tax'),
                reviewed_data.get('tds')
            ))
            cur.execute('''
                INSERT INTO UserFinancials (session_id, gross_salary, basic_salary, hra_received, rent_paid, deduction_80c, deduction_80d, standard_deduction, professional_tax, tds, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ''', (
                session_id,
                reviewed_data.get('gross_salary'),
                reviewed_data.get('basic_salary'),
                reviewed_data.get('hra_received'),
                reviewed_data.get('rent_paid'),
                reviewed_data.get('deduction_80c'),
                reviewed_data.get('deduction_80d'),
                reviewed_data.get('standard_deduction', 50000),
                reviewed_data.get('professional_tax'),
                reviewed_data.get('tds')
            ))
            print('Inserting TaxComparison:', (
                session_id,
                comparison['tax_old_regime'],
                comparison['tax_new_regime'],
                comparison['best_regime'],
                comparison['selected_regime']
            ))
            cur.execute('''
                INSERT INTO TaxComparison (session_id, tax_old_regime, tax_new_regime, best_regime, selected_regime, created_at)
                VALUES (%s,%s,%s,%s,%s,NOW())
            ''', (
                session_id,
                comparison['tax_old_regime'],
                comparison['tax_new_regime'],
                comparison['best_regime'],
                comparison['selected_regime']
            ))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            import traceback
            print('DB Exception:', traceback.format_exc())
            return f"<h2>Database error:</h2><pre>{e}</pre>"
        return render_template('results.html',
            tax_old=comparison['tax_old_regime'],
            tax_new=comparison['tax_new_regime'],
            best=comparison['best_regime'],
            selected=comparison['selected_regime'],
            data=reviewed_data
        )
    # GET: pre-fill fields from query args if available
    return render_template('form.html',
        gross_salary=request.args.get('gross_salary'),
        basic_salary=request.args.get('basic_salary'),
        hra_received=request.args.get('hra_received'),
        rent_paid=request.args.get('rent_paid'),
        deduction_80c=request.args.get('deduction_80c'),
        deduction_80d=request.args.get('deduction_80d'),
        standard_deduction=request.args.get('standard_deduction', 50000),
        professional_tax=request.args.get('professional_tax'),
        tds=request.args.get('tds'),
        selected_regime='new'
    )

if __name__ == '__main__':
    app.run(debug=True) 