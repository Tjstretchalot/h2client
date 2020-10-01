import asyncio
from h2client.http2_connection import HTTP2Connection
import io
import json


USER_AGENT = 'H2ClientExamples/1 by /u/Tjstretchalot (+https://github.com/tjstretchalot/h2client)'


async def main():
    h2conn = HTTP2Connection('postman-echo.com')
    await h2conn.open()

    print('Performing a GET request')
    out = io.BytesIO()
    headers = await h2conn.get(
        '/get?limit=3',
        {
            'user-agent': USER_AGENT,
            'accept': 'application/json'
        },
        out
    )

    print('Headers:')
    for key, val in headers.items():
        print(f'  {key}: {val}')

    print()
    print('Body:')
    pretty_body = json.dumps(
        json.loads(out.getvalue().decode('utf-8')),
        indent=2, sort_keys=True
    )
    print(pretty_body)

    await h2conn.close()


if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())

    pending = asyncio.all_tasks(loop)
    while pending:
        loop.run_until_complete(asyncio.wait(pending, return_when=asyncio.ALL_COMPLETED))
        pending = asyncio.all_tasks(loop)
