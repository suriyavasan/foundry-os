"""Turn what a person types (a name, a slug, the first characters of an id) into one API object."""

from __future__ import annotations

from app.cli.client import CliError
from app.cli.context import CliState

_UUID_LEN = 36


def resolve(st: CliState, kind: str, ref: str, *, label: str | None = None, **list_params) -> dict:
    """``kind`` is the collection name (``agents``, ``tasks``, ``approvals``, ``notes``, ``skills``,
    ``goals``). Matches an exact id, then a unique id prefix (>= 6 chars), then a name/title/slug
    (case-insensitive)."""
    label = label or kind.rstrip("s")
    client = st.client()
    if len(ref) == _UUID_LEN:
        try:
            return client.get(st.cpath(f"/{kind}/{ref}"))
        except CliError as exc:
            if exc.exit_code != 5:
                raise
    items = client.paged(st.cpath(f"/{kind}"), **list_params)
    lowered = ref.lower()
    exact = [i for i in items if i.get("id") == ref]
    if not exact and len(ref) >= 6:
        exact = [i for i in items if str(i.get("id", "")).startswith(ref)]
    if not exact:
        exact = [
            i
            for i in items
            if lowered
            in {str(i.get(k, "")).lower() for k in ("name", "title", "slug", "body") if i.get(k)}
        ]
    if not exact:
        raise CliError(f"No {label} matches {ref!r}.", exit_code=5, code="not_found")
    if len(exact) > 1:
        names = ", ".join(
            f"{i.get('name') or i.get('title') or i.get('slug') or ''} ({str(i['id'])[:8]})"
            for i in exact[:6]
        )
        raise CliError(
            f"{ref!r} matches several {kind}: {names}. Use more of the id.", code="ambiguous"
        )
    return exact[0]
