import asyncio, re, ssl
from argparse     import ArgumentParser
from configparser import ConfigParser
from typing       import List, Tuple

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.backends import default_backend
from async_timeout import timeout as timeout_

from irctokens import build, Line
from ircrobots import Bot as BaseBot
from ircrobots import Server as BaseServer
from ircrobots import ConnectionParams, SASLUserPass

CHANS:   List[str] = []
BAD:     List[str] = []
ACTIONS: List[str] = []

PATTERNS: List[Tuple[str, str, str]] = [
    # match @[...]/ip.[...]
    (r"^.+/ip\.(?P<ip>[^/]+)$", "*!*@*/ip.{IP}")
]

TLS = ssl.SSLContext(ssl.PROTOCOL_TLS)
TLS.options |= ssl.OP_NO_SSLv2
TLS.options |= ssl.OP_NO_SSLv3
TLS.load_default_certs()

async def _common_name(ip: str, port: int) -> str:
    reader, writer = await asyncio.open_connection(ip, port, ssl=TLS)
    der_cert = writer.transport._ssl_protocol._sslpipe.ssl_object.getpeercert(True)
    writer.close()
    await writer.wait_closed()

    pem_cert = ssl.DER_cert_to_PEM_cert(der_cert).encode("ascii")
    cert     = x509.load_pem_x509_certificate(pem_cert, default_backend())
    cns      = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return cns[0].value

class Server(BaseServer):
    async def _act(self,
            line: Line,
            mask: str,
            ip:   str,
            cn:   str):
        data = {
            "CHAN": line.params[0],
            "NICK": line.hostmask.nickname,
            "MASK": mask,
            "IP":   ip,
            "CN":   cn
        }

        for action in ACTIONS:
            action_f = action.format(**data)
            await self.send_raw(action_f)

    async def line_read(self, line: Line):
        print(f"{self.name} < {line.format()}")
        if   line.command == "001":
            await self.send(build("JOIN", CHANS))
        elif (line.command == "JOIN" and
                not self.is_me(line.hostmask.nickname)):
            for pattern, mask in PATTERNS:
                match = re.search(pattern, line.hostmask.hostname)
                if match:
                    ip     = match.group("ip")
                    mask_f = mask.format(IP=ip)

                    try:
                        async with timeout_(4):
                            common_name = await _common_name(ip, 443)
                    except TimeoutError:
                        print("timeout")
                        pass
                    else:
                        if common_name in BAD:
                            await self._act(line, mask_f, ip, common_name)

    async def line_send(self, line: Line):
        print(f"{self.name} > {line.format()}")

class Bot(BaseBot):
    def create_server(self, name: str):
        return Server(self, name)

async def main(
        hostname:  str,
        nickname:  str,
        sasl_user: str,
        sasl_pass: str,
        chans:     List[str],
        bad:       List[str],
        actions:   List[str]):
    global CHANS, BAD, ACTIONS
    CHANS   = chans
    BAD     = bad
    ACTIONS = actions

    bot = Bot()
    params = ConnectionParams(nickname, hostname, 6697, True)
    params.sasl = SASLUserPass(sasl_user, sasl_pass)

    await bot.add_server("server", params)
    await bot.run()

if __name__ == "__main__":
    parser = ArgumentParser(
        description="Catch VPN users by :443 TLS certificate common-name")
    parser.add_argument("config")
    args = parser.parse_args()

    config = ConfigParser()
    config.read(args.config)

    hostname  = config["bot"]["hostname"]
    nickname  = config["bot"]["nickname"]
    sasl_user = config["bot"]["sasl-username"]
    sasl_pass = config["bot"]["sasl-password"]
    chans     = config["bot"]["chans"].split(",")
    bad       = config["bot"]["bad"].split(",")
    actions   = config["bot"]["actions"].split(";")

    asyncio.run(main(
        hostname,
        nickname,
        sasl_user,
        sasl_pass,
        chans,
        bad,
        actions
    ))