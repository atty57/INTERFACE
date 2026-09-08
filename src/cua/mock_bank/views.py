"""HTML for the mock core-banking app.

Deliberately hostile to automation, in the ways real back-office software is hostile:
nested framesets, layout carried by tables, and no ids, classes, or data attributes
anywhere. Inputs carry a ``title`` only, which is the accessible-name fallback a real
legacy app happens to give you; the ``no_names`` fault strips even that.
"""

CHROME = """<html><head><title>{title}</title></head>
<body bgcolor="#f4f4ef" text="#111111" link="#00308f" vlink="#00308f">
<font face="Verdana,Arial" size="2">
{body}
</font></body></html>"""


def page(title: str, body: str, strip_names: bool = False) -> str:
    html = CHROME.format(title=title, body=body)
    if strip_names:
        html = _strip_titles(html)
    return html


def _strip_titles(html: str) -> str:
    """Remove title="..." from form controls, leaving anchor text as the only signal."""
    out, i = [], 0
    while True:
        j = html.find(' title="', i)
        if j == -1:
            out.append(html[i:])
            return "".join(out)
        end = html.find('"', j + 8)
        out.append(html[i:j])
        i = end + 1


def frameset_root() -> str:
    return """<html><head><title>ACME Core Banking</title></head>
<frameset rows="64,*" border="1">
  <frame name="banner" src="/banner" scrolling="no">
  <frame name="main" src="/main">
  <noframes><body>This terminal requires frame support.</body></noframes>
</frameset></html>"""


def frameset_main() -> str:
    return """<html><head><title>ACME Core Banking</title></head>
<frameset cols="168,*" border="1">
  <frame name="nav" src="/nav">
  <frame name="content" src="/search">
  <noframes><body>This terminal requires frame support.</body></noframes>
</frameset></html>"""


def banner() -> str:
    return page(
        "ACME",
        """<table width="100%" cellpadding="4" cellspacing="0" border="0" bgcolor="#00308f">
<tr><td><font face="Verdana" size="4" color="#ffffff"><b>ACME CORE</b></font></td>
<td align="right"><font face="Verdana" size="1" color="#ffffff">
Release 9.4.2 &nbsp; Terminal 07</font></td></tr></table>""",
    )


def nav() -> str:
    return page(
        "Menu",
        """<table cellpadding="3" cellspacing="0" border="0">
<tr><td><b>Servicing</b></td></tr>
<tr><td><a href="/search" target="content">Member Search</a></td></tr>
<tr><td><a href="/search" target="content">Account Inquiry</a></td></tr>
<tr><td><a href="/signoff" target="_top">Sign Off</a></td></tr>
</table>""",
    )


def signon(message: str = "") -> str:
    banner_row = (
        f'<tr><td colspan="2"><font color="#a00000"><b>{message}</b></font></td></tr>'
        if message
        else ""
    )
    return page(
        "Sign On",
        f"""<br><table align="center" cellpadding="6" cellspacing="0" border="1" bgcolor="#ffffff">
<tr><td colspan="2" bgcolor="#dddddd"><b>Operator Sign On</b></td></tr>
{banner_row}
<form method="post" action="/signon">
<tr><td align="right">User ID:</td>
    <td><input type="text" name="userid" size="18" title="User ID"></td></tr>
<tr><td align="right">Password:</td>
    <td><input type="password" name="password" size="18" title="Password"></td></tr>
<tr><td></td><td><input type="submit" value="Sign On"></td></tr>
</form>
</table>
<br><table align="center" border="0"><tr><td>
<font size="1">Unauthorized access is prohibited. All activity is logged.</font>
</td></tr></table>""",
    )


def search(message: str = "", member_id: str = "", strip_names: bool = False) -> str:
    banner_row = (
        f'<tr><td colspan="2"><font color="#a00000"><b>{message}</b></font></td></tr>'
        if message
        else ""
    )
    return page(
        "Member Search",
        f"""<table cellpadding="6" cellspacing="0" border="0" width="100%">
<tr><td bgcolor="#dddddd"><b>Member Search</b></td></tr></table>
<table cellpadding="6" cellspacing="0" border="1" bgcolor="#ffffff">
{banner_row}
<form method="get" action="/detail">
<tr><td align="right">Member ID:</td>
    <td><input type="text" name="member_id" size="14" value="{member_id}"
        title="Member ID"></td></tr>
<tr><td align="right">Surname:</td>
    <td><input type="text" name="surname" size="24" title="Surname"></td></tr>
<tr><td></td><td><input type="submit" value="Search"></td></tr>
</form>
</table>
<br><font size="1">Enter a numeric Member ID. Wildcards are not supported on this release.</font>""",
        strip_names=strip_names,
    )


def detail(member_id: str, rec: dict[str, str], extra: str = "", strip_names: bool = False) -> str:
    return page(
        "Member Detail",
        f"""<table cellpadding="6" cellspacing="0" border="0" width="100%">
<tr><td bgcolor="#dddddd"><b>Member Detail</b></td></tr></table>
{extra}
<table cellpadding="4" cellspacing="0" border="1" bgcolor="#ffffff">
<tr><td>Member ID</td><td>{member_id}</td></tr>
<tr><td>Name</td><td>{rec["name"]}</td></tr>
<tr><td>Status</td><td>{rec["status"]}</td></tr>
<tr><td>Branch</td><td>{rec["branch"]}</td></tr>
<tr><td>Opened</td><td>{rec["opened"]}</td></tr>
</table>
<br>
<table cellpadding="4" cellspacing="0" border="1" bgcolor="#ffffff">
<tr bgcolor="#dddddd"><td><b>Account</b></td><td><b>Balance</b></td></tr>
<tr><td>Checking</td><td>{rec["checking"]}</td></tr>
<tr><td>Savings</td><td>{rec["savings"]}</td></tr>
</table>
<br>
<table cellpadding="4" cellspacing="0" border="0">
<tr><td><a href="/search">Back to Search</a></td>
    <td><a href="/close?member_id={member_id}">Close Account</a></td></tr>
</table>""",
        strip_names=strip_names,
    )


def dialog(member_id: str) -> str:
    """An unexpected interstitial. Dismissable by its own control, like the real thing."""
    return page(
        "Notice",
        f"""<br><table align="center" cellpadding="10" cellspacing="0" border="2"
bgcolor="#ffffe0" role="dialog"><tr><td>
<b>System Notice</b><br><br>
Scheduled maintenance window begins at 23:00. Batch posting may be delayed.<br><br>
<a href="/dismiss?member_id={member_id}">Continue</a>
</td></tr></table>""",
    )


def message_page(title: str, message: str, detail_text: str = "") -> str:
    return page(
        title,
        f"""<table cellpadding="6" cellspacing="0" border="0" width="100%">
<tr><td bgcolor="#dddddd"><b>{title}</b></td></tr></table>
<br><table cellpadding="6" cellspacing="0" border="1" bgcolor="#ffffff">
<tr><td><font color="#a00000"><b>{message}</b></font></td></tr>
{f"<tr><td>{detail_text}</td></tr>" if detail_text else ""}
</table>
<br><a href="/search">Back to Search</a>""",
    )
