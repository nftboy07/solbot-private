"""
Automates the creation of Notion 30-day trials via Playwright.
Requires a headless browser environment.
"""

import sys
import asyncio
try:
    from playwright.async_api import async_playwright
except ImportError:
    print("Playwright not installed. Run 'pip install playwright && playwright install'")
    sys.exit(1)

async def generate_notion_trial(email: str):
    """
    1. Go to Notion.so
    2. Sign up with temp email
    3. Complete the 'Personal' onboarding to trigger the trial
    4. Extract the token_v2 cookie
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        print(f"Attempting signup for {email}...")
        await page.goto("https://www.notion.so/signup")
        await page.fill('input[type="email"]', email)
        await page.click('button:has-text("Continue")')
        
        # Note: At this point, Notion sends a 'Magic Link' or OTP.
        # This script would need to poll a temp-mail API to get the code.
        print("Waiting for manual OTP entry or Magic Link interaction...")
        
        # After login:
        # await page.click('text=For personal use')
        # ... onboarding steps ...
        
        cookies = await context.cookies()
        token_v2 = next((c['value'] for c in cookies if c['name'] == 'token_v2'), None)
        
        await browser.close()
        return token_v2

if __name__ == "__main__":
    # Usage: python notion_trial_generator.py harry_temp_123@example.com
    if len(sys.argv) > 1:
        asyncio.run(generate_notion_trial(sys.argv[1]))
