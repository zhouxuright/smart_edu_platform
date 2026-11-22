
import requests
import time
import os
from PIL import Image, ImageDraw, ImageFont
import json

BASE_URL = "http://127.0.0.1:8000/api"
EMAIL = "student@example.com"
PASSWORD = "password123"

def create_dummy_image(filename):
    img = Image.new('RGB', (800, 600), color=(255, 255, 255))
    d = ImageDraw.Draw(img)
    try:
        # Try to load a font, or use default
        font = ImageFont.truetype("arial.ttf", 20)
    except:
        font = ImageFont.load_default()
    
    # Draw some math problem
    text = "Solve for x:\n2x + 5 = 15\n\nExplain your steps."
    d.text((50, 50), text, fill=(0, 0, 0), font=font)
    
    img.save(filename)
    return filename

def login():
    print("Logging in...")
    try:
        response = requests.post(f"{BASE_URL}/login", json={"email": EMAIL, "password": PASSWORD})
        response.raise_for_status()
        return response.json()["access_token"]
    except Exception as e:
        print(f"Login failed: {e}")
        # Try registering if login fails
        print("Trying to register...")
        try:
            requests.post(f"{BASE_URL}/register", json={
                "email": EMAIL, 
                "password": PASSWORD,
                "full_name": "Test Student",
                "role": "student"
            })
            response = requests.post(f"{BASE_URL}/login", json={"email": EMAIL, "password": PASSWORD})
            response.raise_for_status()
            return response.json()["access_token"]
        except Exception as e2:
            print(f"Registration/Login failed: {e2}")
            return None

def verify_flow():
    token = login()
    if not token:
        return

    headers = {"Authorization": f"Bearer {token}"}
    
    # 1. Create dummy image
    img_filename = "test_mistake.png"
    create_dummy_image(img_filename)
    
    try:
        # 2. Upload image
        print(f"Uploading {img_filename} to /mistakes/batch_image_upload...")
        with open(img_filename, "rb") as f:
            files = [("files", (img_filename, f, "image/png"))]
            response = requests.post(f"{BASE_URL}/mistakes/batch_image_upload", headers=headers, files=files)
        
        if response.status_code != 200:
            print(f"Upload failed: {response.status_code} {response.text}")
            return

        result = response.json()
        print(f"Upload result: {json.dumps(result, indent=2)}")
        
        mistakes = result.get("mistakes", [])
        if not mistakes:
            print("No mistakes returned from upload.")
            return
            
        mistake_id = mistakes[0]["id"]
        print(f"Created mistake ID: {mistake_id}")
        
        # 3. Poll for analysis
        print("Waiting for background analysis (timeout 60s)...")
        start_time = time.time()
        while time.time() - start_time < 60:
            response = requests.get(f"{BASE_URL}/mistakes/{mistake_id}", headers=headers)
            if response.status_code == 200:
                mistake_data = response.json()
                analysis = mistake_data.get("analysis_result")
                
                if analysis:
                    print("Analysis completed!")
                    print(f"Analysis Result: {json.dumps(analysis, ensure_ascii=False, indent=2)}")
                    
                    # 4. Check for similar problem
                    similar = analysis.get("similar_problem")
                    if similar and (similar.get("question") or similar.get("content")):
                        print("SUCCESS: Similar problem generated.")
                        return
                    else:
                        print("WARNING: Analysis found, but 'similar_problem' is empty or missing.")
                        # It might be valid if the LLM didn't generate it, but for verification we want it.
                        return
                else:
                    print("Analysis pending...", end="\r")
            else:
                print(f"Error fetching mistake: {response.status_code}")
            
            time.sleep(2)
            
        print("Timeout waiting for analysis.")
        
    finally:
        # Cleanup
        if os.path.exists(img_filename):
            try:
                os.remove(img_filename)
            except:
                pass

if __name__ == "__main__":
    verify_flow()
