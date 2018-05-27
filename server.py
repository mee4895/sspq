import asyncio
from enum import Enum
from sspq import Message, MessageType, MessageException, read_message, Client, SSPQ_PORT
from argparse import ArgumentParser, ArgumentTypeError


class OrderedEnum(Enum):
    def __ge__(self, other):
        if self.__class__ is other.__class__:
            return self.value >= other.value
        return NotImplemented
    def __gt__(self, other):
        if self.__class__ is other.__class__:
            return self.value > other.value
        return NotImplemented
    def __le__(self, other):
        if self.__class__ is other.__class__:
            return self.value <= other.value
        return NotImplemented
    def __lt__(self, other):
        if self.__class__ is other.__class__:
            return self.value < other.value
        return NotImplemented


class LogLevel(OrderedEnum):
    FAIL = '1'
    WARN = '2'
    INFO = '3'
    DBUG = '4'

    @classmethod
    def parse(cls, string: str) -> super:
        string = string.lower()
        if string == 'fail':
            return cls.FAIL
        if string == 'warn':
            return cls.WARN
        if string == 'info':
            return cls.INFO
        if string == 'dbug':
            return cls.DBUG
        raise ArgumentTypeError(str + ' is NOT a valid loglevel')


async def user_handler(reader, writer):
    client = Client(reader=reader, writer=writer, loop=loop)
    if LOG_LEVEL >= LogLevel.INFO:
        print('User{} connected'.format(str(client.address)))

    while True:
        try:
            msg = await read_message(client.reader)
        except MessageException as e:
            if LOG_LEVEL >= LogLevel.WARN:
                print('User{} disconnected because: {}'.format(str(client.address), str(e)))
            client.disconnected = True
            client.message_event.set()
            client.writer.close()
            return
        except EOFError:
            if LOG_LEVEL >= LogLevel.INFO:
                print('User{} disconnected'.format(str(client.address)))
            client.disconnected = True
            client.message_event.set()
            client.writer.close()
            return

        if msg.type == MessageType.SEND:
            if LOG_LEVEL >= LogLevel.DBUG:
                print('Recieved: ' + msg.payload.decode())
            await message_queue.put(msg)
        elif msg.type == MessageType.RECEIVE:
            if client.message is not None:
                if LOG_LEVEL >= LogLevel.WARN:
                    print('Receive Message is going to be droped because client need to confirm his message.')
                    continue
            if LOG_LEVEL >= LogLevel.DBUG:
                print('User{} wants to receive'.format(str(client.address)))
            client.message_event.clear()
            await client_queue.put(client)
        elif msg.type == MessageType.CONFIRM:
            if client.message is None:
                if LOG_LEVEL >= LogLevel.WARN:
                    print('Confirm Message is going to be droped because client has no message to confirm.')
                    continue
            if LOG_LEVEL >= LogLevel.DBUG:
                print('User{} confirms message'.format(str(client.address)))
            client.message = None
            client.message_event.set()
            await asyncio.sleep(0)
        else:
            if LOG_LEVEL >= LogLevel.WARN:
                print('Received unknown packet:\n' + msg.encode().decode())


async def message_handler(message: Message, client: Client):
    client.message = message
    await message.send(client.writer)
    await client.message_event.wait()
    if client.message is not None:
        if client.message.retrys == 0:
            if not NDLQ:
                await dead_letter_queue.put(message)
        else:
            if client.message.retrys != 255:
                client.message.retrys -= 1
            await message_queue.put(message)


async def queue_handler(loop):
    while True:
        msg = await message_queue.get()
        client = await client_queue.get()
        while client.disconnected:
            client = await client_queue.get()
        asyncio.ensure_future(message_handler(msg, client), loop=loop)


# Entry Point
if __name__ == "__main__":
    # Setup argparse
    parser = ArgumentParser(description='SSPQ Server - Super Simple Python Queue Server', add_help=True)
    parser.add_argument('--host', action='store', default='127.0.0.1', required=False, help='Set the host address. Juse 0.0.0.0 to make the server public', dest='host', metavar='<host>')
    parser.add_argument('-p', '--port', action='store', default=SSPQ_PORT, type=int, required=False, help='Set the port the server listens to', dest='port', metavar='<port>')
    parser.add_argument('-ll', '--loglevel', action='store', default='info', type=LogLevel.parse, choices=[
        LogLevel.FAIL, LogLevel.WARN, LogLevel.INFO, LogLevel.DBUG
    ], required=False, help='Set the appropriate log level for the output on stdout. Possible values are: [ fail | warn | info | dbug ]', dest='log_level', metavar='<log-level>')
    parser.add_argument('-ndlq', '--no-dead-letter-queue', action='store_true', required=False, help='Flag to dissable the dead letter queueing, failed packages are then simply dropped after the retrys run out.', dest='ndlq')
    parser.add_argument('-v', '--version', action='version', version='%(prog)s v.0.2.0')
    args = parser.parse_args()

    # set 'global' log level
    LOG_LEVEL = args.log_level
    NDLQ = args.ndlq

    # Setup asyncio & queues
    loop = asyncio.get_event_loop()
    message_queue = asyncio.Queue(loop=loop)
    client_queue = asyncio.Queue(loop=loop)
    dead_letter_queue = asyncio.Queue(loop=loop)
    coro = asyncio.start_server(user_handler, args.host, args.port, loop=loop)
    server = loop.run_until_complete(coro)
    queue_worker = asyncio.ensure_future(queue_handler(loop=loop), loop=loop)

    # Serve requests until Ctrl+C is pressed
    print('Serving on {}'.format(server.sockets[0].getsockname()))
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass

    # Close the server
    server.close()
    queue_worker.cancel()
    loop.run_until_complete(server.wait_closed())
    loop.close()
