import asyncio
import os
from groq import AsyncGroq
from dotenv import load_dotenv

load_dotenv(os.path.join('config', '.env'), encoding='latin-1')

async def test():
    key = os.environ.get('LLM_API_KEY', '')
    print('Key:', key[:5] + '...' if key else 'MISSING')
    client = AsyncGroq(api_key=key)
    print('Connecting...')
    try:
        resp = await client.chat.completions.create(
            model='llama-3.3-70b-versatile',
            messages=[{'role': 'user', 'content': 'hi'}],
            max_tokens=10
        )
        print('Success:', resp.choices[0].message.content)
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(test())
