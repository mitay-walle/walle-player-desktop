"""UPnP on the home router: find it, read its internet address, open / close the server's port.  Qt-free, no deps.

With a static («white») IP from the provider the phone reaches the computer through a forwarded port on the router;
most home routers (Cudy too) let programs ask for that over UPnP IGD instead of a manual setting.  The router's
WAN address also tells whether the provider's IP is «grey» (CGNAT) - then forwarding cannot help.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import urllib.parse
import urllib.request
from xml.etree import ElementTree

log = logging.getLogger(__name__)
SSDP = ("239.255.255.250", 1900)
SERVICES = ("urn:schemas-upnp-org:service:WANIPConnection:2", "urn:schemas-upnp-org:service:WANIPConnection:1",
            "urn:schemas-upnp-org:service:WANPPPConnection:1")
DESCRIPTION = "Walle Player"


def is_grey(ip: str | None) -> bool:
    """A provider's internal address: nobody outside can connect to it (CGNAT 100.64/10, private ranges)."""
    try:
        a = ipaddress.ip_address(ip or "")
    except ValueError:
        return True
    return a.is_private or a in ipaddress.ip_network("100.64.0.0/10") or a.is_loopback or a.is_link_local


class Router:
    """The router's UPnP control point: ``Router.find(lan_ip)`` or None when UPnP is off / absent."""

    def __init__(self, control_url: str, service: str) -> None:
        self.control_url, self.service = control_url, service

    @classmethod
    def find(cls, lan_ip: str | None = None, timeout: float = 3.0) -> "Router | None":
        msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\n"
               "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n").encode()
        locations: list[str] = []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as s:
            s.settimeout(timeout)
            if lan_ip:                       # through the home network card, not a VPN adapter
                try:
                    s.bind((lan_ip, 0))
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(lan_ip))
                except OSError:
                    pass
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            try:
                s.sendto(msg, SSDP)
                while True:
                    data, _ = s.recvfrom(4096)
                    m = re.search(rb"(?im)^location:\s*(\S+)", data)
                    if m and m.group(1).decode() not in locations:
                        locations.append(m.group(1).decode())
            except (socket.timeout, OSError):
                pass
        for loc in locations:
            try:
                router = cls._from_description(loc)
            except Exception:  # noqa: BLE001 - a device with a broken description: try the next one
                log.debug("UPnP description %s unusable", loc, exc_info=True)
                continue
            if router:
                return router
        return None

    @classmethod
    def _from_description(cls, location: str) -> "Router | None":
        xml = urllib.request.urlopen(location, timeout=4).read()
        root = ElementTree.fromstring(xml)
        ns = {"d": "urn:schemas-upnp-org:device-1-0"}
        for svc in root.iter("{urn:schemas-upnp-org:device-1-0}service"):
            stype = svc.findtext("d:serviceType", default="", namespaces=ns)
            if stype in SERVICES:
                ctrl = svc.findtext("d:controlURL", default="", namespaces=ns)
                return cls(urllib.parse.urljoin(location, ctrl), stype)
        return None

    def _soap(self, action: str, args: dict[str, str | int] | None = None) -> dict[str, str]:
        body = "".join(f"<{k}>{v}</{k}>" for k, v in (args or {}).items())
        env = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
               f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
               f'<u:{action} xmlns:u="{self.service}">{body}</u:{action}></s:Body></s:Envelope>').encode()
        req = urllib.request.Request(self.control_url, data=env, method="POST", headers={
            "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{self.service}#{action}"'})
        try:
            xml = urllib.request.urlopen(req, timeout=5).read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            m = re.search(r"<errorDescription>(.*?)</errorDescription>", detail)
            raise RuntimeError(m.group(1) if m else f"HTTP {e.code}") from None
        out = {}
        for el in ElementTree.fromstring(xml).iter():
            if el.text and not list(el):
                out[el.tag.split("}")[-1]] = el.text
        return out

    def external_ip(self) -> str | None:
        return self._soap("GetExternalIPAddress").get("NewExternalIPAddress")

    def add_port(self, external: int, internal: int, lan_ip: str) -> None:
        self._soap("AddPortMapping", {"NewRemoteHost": "", "NewExternalPort": external, "NewProtocol": "TCP",
                                      "NewInternalPort": internal, "NewInternalClient": lan_ip, "NewEnabled": 1,
                                      "NewPortMappingDescription": DESCRIPTION, "NewLeaseDuration": 0})

    def remove_port(self, external: int) -> None:
        self._soap("DeletePortMapping", {"NewRemoteHost": "", "NewExternalPort": external, "NewProtocol": "TCP"})
