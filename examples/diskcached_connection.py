import asyncio
from h2client.diskcached_connection import DiskcachedConnection
import io
import time


USER_AGENT = 'H2ClientExamples/1 by /u/Tjstretchalot (+https://github.com/tjstretchalot/h2client)'


async def main():
    dc_conn = DiskcachedConnection('postman-echo.com')
    print('Performing a GET request')
    out = io.BytesIO()

    start_time = time.time()
    headers = await dc_conn.get(
        '/get',
        {
            'user-agent': USER_AGENT,
            'accept': 'application/json'
        },
        out
    )
    total_time = time.time() - start_time
    print(f'Finished GET request in {total_time} seconds')

    print('Headers:')
    for key, val in headers.items():
        print(f'  {key}: {val}')

    print()
    print('Body:')
    pretty_body = out.getvalue().decode('utf-8')
    print(pretty_body)

    await dc_conn.close()


if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())

    pending = asyncio.all_tasks(loop)
    while pending:
        loop.run_until_complete(asyncio.wait(pending, return_when=asyncio.ALL_COMPLETED))
        pending = asyncio.all_tasks(loop)
