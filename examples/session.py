"""Make some requests using the postman-echo service run by
https://www.postman.com/. h2client is not in any way affiliated with or
endorsed by Postman.
"""
import asyncio
from h2client import Session
import json


USER_AGENT ='H2ClientExamples/1 by /u/Tjstretchalot (+https://github.com/tjstretchalot/h2client)'


async def main():
    client = Session(
        'postman-echo.com',
        default_headers={
            'user-agent': USER_AGENT,
            'accept': 'application/json'
        }
    )

    async with client.conn() as conn:
        responses = await asyncio.gather(
            conn.get('/get', params={'foo1': 'bar1'}),
            conn.post('/post', json={'payload': 'asdf'}),
            conn.delete('/delete', data=b'look some data!', headers={
                'content-type': 'text/plain'
            })
        )

    for response in responses:
        response: ResponseData
        print(f'{response.method} {response.path} - {response.status_code} {response.status_text}')
        print('Request Headers:')
        for key, val in response.request_headers.items():
            print(f'  {key}: {val}')

        print('Headers:')
        for key, val in response.headers.items():
            print(f'  {key}: {val}')

        print()
        print('Body (interpreted):')
        print(json.dumps(response.json(), indent=2, sort_keys=True))


if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())

    # Wait for the `ensure_future`s to finish cleanly
    pending = asyncio.all_tasks(loop)
    while pending:
        loop.run_until_complete(asyncio.wait(pending, return_when=asyncio.ALL_COMPLETED))
        pending = asyncio.all_tasks(loop)
