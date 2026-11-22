from pypdf import PdfReader

try:
    reader = PdfReader("e:\\smart_edu_platform\\周栩-Python+AI_Agent大模型应用开发工程师.pdf")
    text = ""
    for page in reader.pages:
        text += page.extract_text() + "\n"
    print(text)
except Exception as e:
    print(f"Error reading PDF: {e}")
