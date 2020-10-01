import asyncio
from h2client.connection import Connection
from h2client.stream_connection import StreamConnection
from h2client.request_connection import RequestConnection
import io


USER_AGENT = 'H2ClientExamples/1 by /u/Tjstretchalot (+https://github.com/tjstretchalot/h2client)'


async def main():
    rconn = RequestConnection(StreamConnection(Connection()))

    print('Opening connection...')
    await rconn.sconn.conn.open('postman-echo.com')

    print('Performing GET request to /get...')

    itr = rconn.request(
        'GET',
        'postman-echo.com',
        '/get',
        [('user-agent', USER_AGENT)],
        None
    )

    headers = await itr.__anext__()
    print('Headers:')
    for (key, val) in headers:
        name = key.decode('utf-8')
        sval = val.decode('utf-8')
        print(f'  {name}: {sval}')

    incoming_data = io.BytesIO()
    while True:
        body = await itr.__anext__()
        if body is None:
            break

        incoming_data.write(body)

    print('Body:')
    print(incoming_data.getbuffer().tobytes().decode('utf-8'))

    trailers = await itr.__anext__()
    print('Trailers:')
    for (key, val) in trailers:
        name = key.decode('utf-8')
        sval = val.decode('utf-8')
        print(f'  {name}: {sval}')

    await rconn.close()


if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())

    pending = asyncio.all_tasks(loop)
    while pending:
        loop.run_until_complete(asyncio.wait(pending, return_when=asyncio.ALL_COMPLETED))
        pending = asyncio.all_tasks(loop)
