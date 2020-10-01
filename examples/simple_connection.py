import asyncio
from h2client.simple_connection import SimpleConnection, ResponseData
import io
import json
import time


USER_AGENT ='H2ClientExamples/1 by /u/Tjstretchalot (+https://github.com/tjstretchalot/h2client)'


async def main():
    si_conn = SimpleConnection(
        'postman-echo.com',
        default_headers={
            'user-agent': USER_AGENT,
            'accept': 'application/json'
        }
    )
    responses = await asyncio.gather(
        si_conn.get('/get', params={'foo1': 'bar1'}),
        si_conn.post('/post', json={'payload': 'foo'})
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

    await si_conn.close()


if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())

    pending = asyncio.all_tasks(loop)
    while pending:
        loop.run_until_complete(asyncio.wait(pending, return_when=asyncio.ALL_COMPLETED))
        pending = asyncio.all_tasks(loop)
