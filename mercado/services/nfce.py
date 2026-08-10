from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .parser import parse_receipt_text


class UnsafeReceiptURL(ValueError):
    pass


def validate_public_url(url: str, allowed_hosts: list[str]) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeReceiptURL("A URL precisa usar HTTP ou HTTPS.")
    host = parsed.hostname.lower().rstrip(".")
    if not any(host == allowed or host.endswith("." + allowed) for allowed in allowed_hosts):
        raise UnsafeReceiptURL("O domínio não está na lista de portais fiscais permitidos.")
    if parsed.port not in {None, 80, 443}:
        raise UnsafeReceiptURL("Porta não permitida.")
    for address in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM):
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise UnsafeReceiptURL("O endereço não é público.")
    return parsed.geturl()


def fetch_nfce(url: str, allowed_hosts: list[str]) -> dict:
    current = validate_public_url(url, allowed_hosts)
    response = None
    for _ in range(4):
        response = requests.get(
            current,
            timeout=(5, 20),
            allow_redirects=False,
            headers={"User-Agent": "ControleMercado/1.0"},
        )
        if response.is_redirect:
            current = validate_public_url(
                urljoin(current, response.headers["location"]), allowed_hosts
            )
            continue
        break
    if response is None:
        raise RuntimeError("Não foi possível consultar o cupom.")
    response.raise_for_status()
    if len(response.content) > 2_000_000:
        raise ValueError("A resposta do portal fiscal é maior que o limite aceito.")
    soup = BeautifulSoup(response.content, "html.parser")
    text = soup.get_text("\n", strip=True)
    result = parse_receipt_text(text)
    result["raw_text"] = text
    result["qr_url"] = current
    return result

