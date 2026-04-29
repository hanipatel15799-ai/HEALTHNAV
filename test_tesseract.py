from dotenv import load_dotenv
import os
import pytesseract

load_dotenv()

tesseract_path = (
    os.getenv("TESSERACT_CMD")
    or os.getenv("TESSERACT_PATH")
    or ""
).strip()

print("Tesseract path:", tesseract_path)

if tesseract_path:
    pytesseract.pytesseract.tesseract_cmd = tesseract_path

print("Version:", pytesseract.get_tesseract_version())