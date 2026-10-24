"""Namecheap: availability, pricing and registration (XML API, ApiUser + ApiKey + whitelisted IP).

Not exercised against the real service in M8: the XML shapes follow Namecheap's documentation and
are covered by recorded fixtures only.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from app.integrations.base import ApiClient, ProviderError, ProviderNotConfigured, usd_to_micros

NS = "{http://api.namecheap.com/xml.response}"
CONTACT_FIELDS = (
    "FirstName",
    "LastName",
    "Address1",
    "City",
    "StateProvince",
    "PostalCode",
    "Country",
    "Phone",
    "EmailAddress",
)


class NamecheapClient(ApiClient):
    provider = "namecheap"
    base_url = "https://api.namecheap.com/xml.response"

    def __init__(self, http, api_key: str, *, api_user: str, username: str, client_ip: str) -> None:
        super().__init__(http, api_key)
        self._key, self._user, self._username, self._ip = api_key, api_user, username, client_ip

    def _call(self, command: str, what: str, **params: Any) -> ET.Element:
        form = {
            "ApiUser": self._user,
            "ApiKey": self._key,
            "UserName": self._username,
            "ClientIp": self._ip,
            "Command": command,
            **params,
        }
        resp = self.request("POST", self.base_url, what=what, data=form, absolute=True)
        text = resp.text
        if "<!DOCTYPE" in text or "<!ENTITY" in text:  # never parse hostile XML
            raise self._fail(resp.status_code, "unexpected XML declaration", what)
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise self._fail(resp.status_code, "response was not XML", what) from exc
        if root.get("Status") != "OK":
            errors = [e.text or "" for e in root.iter(f"{NS}Error")] or [
                e.text or "" for e in root.iter("Error")
            ]
            raise self._fail(200, "; ".join(errors) or "error", what)
        return root

    @staticmethod
    def _find(root: ET.Element, tag: str) -> list[ET.Element]:
        return list(root.iter(f"{NS}{tag}")) or list(root.iter(tag))

    def ping(self) -> dict[str, Any]:
        root = self._call("namecheap.users.getBalances", "credential check")
        row = next(iter(self._find(root, "UserGetBalancesResult")), None)
        return {
            "account": self._username,
            "currency": row.get("Currency") if row is not None else None,
        }

    def price(self, tld: str) -> tuple[int | None, int | None]:
        """(register, renew) one-year price in micro-USD for a TLD."""
        out: dict[str, int | None] = {}
        for action in ("REGISTER", "RENEW"):
            root = self._call(
                "namecheap.users.getPricing",
                "pricing lookup",
                ProductType="DOMAIN",
                ProductCategory="DOMAINS",
                ActionName=action,
                ProductName=tld,
            )
            price = None
            for node in self._find(root, "Price"):
                if (
                    node.get("Duration") == "1"
                    and node.get("DurationType", "YEAR").upper() == "YEAR"
                ):
                    price = usd_to_micros(node.get("YourPrice") or node.get("Price"))
                    break
            out[action] = price
        return out["REGISTER"], out["RENEW"]

    def check(self, domain: str) -> dict[str, Any]:
        root = self._call("namecheap.domains.check", "availability check", DomainList=domain)
        row = next(iter(self._find(root, "DomainCheckResult")), None)
        if row is None:
            raise ProviderError("namecheap: no availability result")
        available = str(row.get("Available", "")).lower() == "true"
        premium = str(row.get("IsPremiumName", "")).lower() == "true"
        price = renewal = None
        if available and premium:
            price = usd_to_micros(row.get("PremiumRegistrationPrice"))
        elif available:
            price, renewal = self.price(domain.rsplit(".", 1)[-1])
        return {
            "domain": domain,
            "available": available,
            "price_micros": price,
            "renewal_price_micros": renewal,
            "premium": premium,
            "min_years": 1,
        }

    def register(self, domain: str, contact: dict[str, str], *, years: int = 1) -> dict[str, Any]:
        missing = [f for f in CONTACT_FIELDS if not contact.get(f)]
        if missing:
            raise ProviderNotConfigured(
                "namecheap: the registrant details are incomplete. Add these to the connected "
                "account's metadata as registrant_<Field>: " + ", ".join(missing)
            )
        params: dict[str, Any] = {"DomainName": domain, "Years": years}
        for kind in ("Registrant", "Tech", "Admin", "AuxBilling"):
            for field in CONTACT_FIELDS:
                params[f"{kind}{field}"] = contact[field]
        root = self._call("namecheap.domains.create", "registration", **params)
        row = next(iter(self._find(root, "DomainCreateResult")), None)
        if row is None or str(row.get("Registered", "")).lower() != "true":
            raise ProviderError("namecheap: the domain was not registered")
        charged = usd_to_micros(row.get("ChargedAmount"))
        return {"domain": domain, "charged_micros": charged, "order_id": row.get("OrderID")}
