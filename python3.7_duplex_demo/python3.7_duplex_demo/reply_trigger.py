"""Select one punctuation-delimited prefix per response, without choosing actions."""

import re
from collections import OrderedDict


class ReplyTrigger:
    def __init__(self):
        self.rows = OrderedDict()

    def feed(self, response_id, text, final=False):
        if not response_id or not isinstance(text, str):
            return None
        row = self.rows.setdefault(str(response_id), {"text": "", "sent": False})
        while len(self.rows) > 256:
            self.rows.popitem(last=False)
        row["text"] = text if final else row["text"] + text
        if final:
            selected = row["text"].strip()
        else:
            selected = ""
            for match in re.finditer(r"[，。！？；,.!?;]", row["text"]):
                prefix = row["text"][:match.end()].strip()
                # Keep punctuation as the boundary; allow short clauses such as
                # "欢迎呀，" and "再见。" without selecting actions here.
                if len(re.sub(r"\W", "", prefix)) >= 2:
                    selected = prefix
        if not selected or selected == row.get("last"):
            return None
        row["last"] = selected
        return selected
