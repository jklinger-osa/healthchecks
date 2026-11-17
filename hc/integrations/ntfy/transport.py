from __future__ import annotations

from textwrap import indent

from django.conf import settings

from hc.api.models import Flip, Notification
from hc.api.transports import HttpTransport, TransportError, get_ping_body


class Ntfy(HttpTransport):
    def priority(self, status: str) -> int:
        if status == "up":
            return self.channel.ntfy.priority_up
        return self.channel.ntfy.priority

    def is_noop(self, status: str) -> bool:
        return self.priority(status) == 0

    def notify(self, flip: Flip, notification: Notification) -> None:
        ntfy = self.channel.ntfy
        from hc.api.models import TokenBucket

        if not TokenBucket.authorize_ntfy(ntfy.url, ntfy.topic):
            raise TransportError("Rate limit exceeded")

        ping = self.last_ping(flip)
        body = None
        if (
            flip.new_status == "down"
            and flip.reason == "fail"
            and ping is not None
            and ping.kind == "fail"
        ):
            body = get_ping_body(ping, maxlen=1000)
            if body:
                body = indent(body, "    ")

        ctx = {
            "flip": flip,
            "check": flip.owner,
            "status": flip.new_status,
            "ping": ping,
            "body": body,
            "down_checks": self.down_checks(flip.owner),
        }
        payload = {
            "topic": self.channel.ntfy.topic,
            "priority": self.priority(flip.new_status),
            "title": self.tmpl("ntfy_title.html", **ctx),
            "message": self.tmpl("ntfy_message.html", **ctx),
            "markdown": True,
            "tags": ["red_circle" if flip.new_status == "down" else "green_circle"],
            "actions": [
                {
                    "action": "view",
                    "label": f"View on {settings.SITE_NAME}",
                    "url": flip.owner.cloaked_url(),
                }
            ],
        }

        url = self.channel.ntfy.url
        headers = {}
        if self.channel.ntfy.token:
            headers["Authorization"] = f"Bearer {self.channel.ntfy.token}"
        elif url == "https://ntfy.sh" and settings.NTFY_SH_TOKEN:
            headers["Authorization"] = f"Bearer {settings.NTFY_SH_TOKEN}"

        self.post(url, headers=headers, json=payload)
