import twisted.protocols.sip as sip
from twisted.internet import reactor
import socket
import datetime


def fix_contact(message):
    header = message.headers['contact'][0]
    if header[0] == '<':
        header = message.headers['contact'][0].replace('<', '').replace('>', '')
    host = sip.parseURL(header).host
    port = sip.parseURL(header).port
    if port is None:
        ip = str(host)
    else:
        ip = str(host) + ':' + str(port)
    message.headers['contact'] = [message.headers['contact'][0].replace(ip, proxyip + ':6000')]
    return message


def get_username(message):
    header = message[0]
    if header[0] == '<':
        header = message[0].replace('<', '').replace('>', '')
    username = sip.parseURL(header).username
    return username


def forward(message, username):
    addr = proxy.registered_users[username]
    try:
        print('To ' + username + ': ' + message.method)
    except AttributeError:
        print('To ' + username + ': ' + sip.statusCodes[message.code])
        if message.code == 486:
            message.phrase = 'Obsadené'
    url = sip.URL(addr[0], port=addr[1])
    proxy.sendMessage(url, message)


def log(text):
    file = open('log.txt', 'a')
    file.write(text)
    file.close()


class Call:

    def __init__(self):
        self.active = False
        self.cancelled = False


class Proxy(sip.Proxy):

    def __init__(self, host, port):
        super().__init__(host, port)
        self.registered_users = dict()
        self.calls = dict()
        sip.statusCodes[202] = 'Accepted'
        sip.statusCodes[486] = 'Obsadené'

    def handle_request(self, message, addr):
        print(self.calls)
        print(message.headers['call-id'])
        if message.method == 'REGISTER':
            # zaregistrujeme a posleme OK
            username = sip.parseURL(message.headers['to'][0]).username
            print('From ' + username + ': REGISTER')
            self.registered_users[username] = addr
            response = self.responseFromRequest(200, message)
            self.deliverResponse(response)
            print('To ' + username + ': OK')

        elif message.method == 'INVITE':
            username_from = get_username(message.headers['from'])
            print('From ' + username_from + ': ' + message.method)

            # vytvorime call
            call = Call()
            self.calls[message.headers['call-id'][0]] = call
            print(self.calls)

            # opravime contact
            message = fix_contact(message)

            # checkujeme, ci je volany zaregistrovany
            username = get_username(message.headers['to'])
            log(str(datetime.datetime.now()) + ' - ' + username_from + ' started a call to ' + username + '\n')
            try:
                addr = self.registered_users[username]
            except KeyError:
                # ak nie je, posleme error
                response = self.responseFromRequest(480, message)
                self.deliverResponse(response)
                print('To ' + username_from + ': ' + sip.statusCodes[480])
            else:
                # ak je, call je aktivny, preposleme INVITE
                call.active = True
                url = sip.URL(addr[0], port=addr[1])
                self.sendMessage(url, message)
                print('To ' + username + ': ' + message.method)

                # posleme Trying
                # response = self.responseFromRequest(100, message)
                # self.deliverResponse(response)
                # print('To ' + username_from + ': ' + sip.statusCodes[100])

        elif message.method == 'ACK':
            call = self.calls[message.headers['call-id'][0]]
            username_from = get_username(message.headers['from'])
            print('From ' + username_from + ': ' + message.method)
            username = get_username(message.headers['to'])
            try:
                # preposleme
                forward(message, username)
            except KeyError:
                # ak prisla odpoved na 480, nepreposielame
                log(str(datetime.datetime.now()) + ' - ' + username + ' is temporarily unavailable ' + '\n')
                pass
            if not call.active or call.cancelled:
                # ACK je odpovedou na 480/486/487/603, vymazeme call
                try:
                    del self.calls[message.headers['call-id'][0]]
                except KeyError:
                    pass
            else:
                # ACK je odpovedou na prijaty hovor
                log(str(datetime.datetime.now()) + ' - ' + username + ' accepted a call from ' + username_from + '\n')

        elif message.method == 'BYE':
            # call je neaktivny, preposleme BYE
            self.calls[message.headers['call-id'][0]].active = False
            username_from = get_username(message.headers['from'])
            print('From ' + username_from + ': ' + message.method)
            username = get_username(message.headers['to'])
            forward(message, username)
            log(str(datetime.datetime.now()) + ' - ' + username_from + ' ended a call with ' + username + '\n')

        elif message.method == 'CANCEL':
            # preposleme CANCEL dalej a cancelneme call
            username_from = get_username(message.headers['from'])
            print('From ' + username_from + ': ' + message.method)
            username = get_username(message.headers['to'])
            forward(message, username)
            self.calls[message.headers['call-id'][0]].cancelled = True
            log(str(datetime.datetime.now()) + ' - ' + username_from + ' cancelled a call to ' + username + '\n')

            # posleme OK volajucemu
            response = self.responseFromRequest(200, message)
            self.deliverResponse(response)
            print('To ' + username_from + ': ' + sip.statusCodes[200])

        elif message.method == 'REFER' or message.method == 'NOTIFY':
            # preposleme REFER/NOTIFY
            username_from = get_username(message.headers['from'])
            print('From ' + username_from + ': ' + message.method)
            username = get_username(message.headers['to'])
            forward(message, username)

    def handle_response(self, message, addr):
        print(self.calls)
        if message.code in [100, 180, 202]:
            # preposleme Trying/Ringing/Accepted
            username = get_username(message.headers['to'])
            print('From ' + username + ': ' + sip.statusCodes[message.code])
            username = get_username(message.headers['from'])
            forward(message, username)

        elif message.code == 200:
            username = get_username(message.headers['to'])
            print('From ' + username + ': ' + sip.statusCodes[message.code])
            call = self.calls[message.headers['call-id'][0]]

            try:
                message = fix_contact(message)
            except KeyError:
                pass

            if not call.cancelled:
                # OK je odpovedou na zdvihnuty hovor alebo CANCEL
                username = get_username(message.headers['from'])
                forward(message, username)
            if not call.active:
                # OK je odpovedou na BYE
                del self.calls[message.headers['call-id'][0]]

        elif message.code in [481, 486, 487, 603]:
            # preposleme spravu
            username_from = get_username(message.headers['to'])
            print('From ' + username_from + ': ' + sip.statusCodes[message.code])
            username = get_username(message.headers['from'])
            forward(message, username)
            if message.code not in [487]:
                if message.code == 481:
                    del self.calls[message.headers['call-id'][0]]
                else:
                    if message.code == 603:
                        log(str(datetime.datetime.now()) + ' - ' + username_from + ' declined a call from ' + username + '\n')
                    elif message.code == 486:
                        log(str(datetime.datetime.now()) + ' - ' + username_from + ' failed to accept a call from ' + '\n')
                    try:
                        self.calls[message.headers['call-id'][0]].active = False
                    except KeyError:
                        pass


hostname = socket.gethostname()
proxyip = socket.gethostbyname(hostname)
print(proxyip)
proxy = Proxy(proxyip, 6000)
reactor.listenUDP(6000, proxy)
reactor.run()
