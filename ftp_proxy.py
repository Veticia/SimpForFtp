import html
import http.server
import socketserver
import ftplib
import os
import urllib.parse
from urllib.parse import unquote
import ftpparser
from datetime import datetime
import mimetypes

PORT = 8000

VERSION = "0.7.1"

INDEX_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>FTP Proxy</title>
    {styles}
</head>
    <body>
        <h1>FTP Proxy</h1>
        <p style="color: red;">{error_message}</p>
        <form action="/" method="get">
        <label>
            FTP Address:
            <input type="text" name="address" required placeholder="ftp.example.com">
        </label>
        <label>
            Username:
            <input type="text" name="username" placeholder="leave blank for anonymous">
        </label>
        <label>
            Password:
            <input type="password" name="password">
        </label>
        <button type="submit">Connect</button>
    </form>
    {demo}
    {footer}
</body>
</html>
"""

FOOTER = f"""
<hr>
<footer>
  <small>
    <a href="https://github.com/Veticia/SimpForFtp">SimpForFtp</a> {VERSION}
    by <a href="https://github.com/Veticia/SimpForFtp/blob/main/LICENSE">Veticia</a>
  </small>
</footer>
"""

STYLES = """
<style>
    label {
        display: block;
    }

    footer {
        margin-top: 20px;
    }
  
    hr {
        border: none;
        border-top: 1px solid #ccc;
    }
</style>
"""

DEMO = ""

if os.path.exists("demo"):
    DEMO = """
<div style="border: 1px solid blue; background-color: #E0FFFF; color: black; padding: 10px; margin: 10px;">
    <div style="float: left; width: 30px;">&#x2139;</div>
    <div style="margin-left: 30px;">This is a fully featured demo instance of <a href="">SimpForFtp</a> proxy.<br>
    For heavy use please consider running your own instance so I can keep this one free for everyone.</div>
</div>
"""

class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    pass


class FTPProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        address = query_params.get('address', [''])[0]
        username = query_params.get('username', [''])[0]
        password = query_params.get('password', [''])[0]

        if parsed_url.path == '/':
            # Check if login credentials were provided
            if address:
                # Test connection to FTP server
                try:
                    ftp = ftplib.FTP(address)
                    ftp.login(username, password)
                    ftp.quit()
                    # If connection is successful, redirect user to proxy path
                    if username and password:
                        proxy_path = f'/proxy/{username}:{password}@{address}/'
                    elif username:
                        proxy_path = f'/proxy/{username}@{address}/'
                    else:
                        proxy_path = f'/proxy/{address}/'
                    self.send_response(302)
                    self.send_header('Location', proxy_path)
                    self.end_headers()
                    return
                except ftplib.all_errors:
                    # If connection fails, display an error message
                    error_message = f'Error: Could not connect to FTP server at {address} with username {username}'
            else:
                error_message = ''

            # Display login form with error message (if any)
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            page_content = INDEX_PAGE.format(error_message=error_message, footer=FOOTER, version=VERSION, styles=STYLES, demo=DEMO)
            self.wfile.write(page_content.encode())
        elif self.path.startswith('/proxy/'):
            parsed_url = urllib.parse.urlparse(self.path)

            # Extract the username, password, and address from the path
            path_parts = parsed_url.path[len('/proxy/'):].split('/', maxsplit=1)
            if '@' in path_parts[0]:
                userinfo, address = path_parts[0].rsplit('@', maxsplit=1)
                if ':' in userinfo:
                    username, password = userinfo.split(':', maxsplit=1)
                else:
                    username = userinfo
                    password = ''
                anonymous = not username
            else:
                address = path_parts[0]
                username = ''
                password = ''
                anonymous = True

            if len(path_parts) > 1:
                path = '/' + unquote(path_parts[1])  # Decode any encoded characters in the path
            else:
                path = '/'

            try:
                ftp = ftplib.FTP(address)

                if anonymous:
                    ftp.login()
                else:
                    ftp.login(username, password)

                trailing_slash = False
                if path.endswith('/'):
                    trailing_slash = True
                    path = path[:-1]  # Remove trailing slash if present

                # Check if the requested path is a file or a directory
                try:
                    ftp.cwd(path)  # Try to change the working directory to the requested path
                    is_directory = True
                    if not trailing_slash:
                        self.send_response(301)
                        self.send_header('Location', f'./{os.path.basename(path)}/')
                        self.end_headers()
                        return
                except ftplib.error_perm:
                    is_directory = False

                if is_directory:
                    sort_order = parsed_url.query
                    self.handle_directory_request(ftp, path, address, username, password, sort_order)
                else:
                    self.handle_file_request(ftp, path)
                ftp.close()


            except ftplib.all_errors:
                # If connection fails, redirect user back to index page
                self.send_response(302)
                self.send_header('Location', '/')
                self.end_headers()
                return
        return

    def handle_directory_request(self, ftp, path, address, username, password, sort_order):
        try:
            # Check if the server supports the MLSD command
            if 'MLSD' in ftp.sendcmd('FEAT'):
                listing = []
                for entry in ftp.mlsd(path):
                    name = entry[0]
                    facts = entry[1]
                    if name in ['.', '..']:
                        continue
                    item_type = 'directory' if facts['type'] == 'dir' else 'file'
                    item_size = int(facts['size']) if 'size' in facts else None
                    item_date = facts['modify'] if 'modify' in facts else ''
                    item_path = f"{path}/{name}"
                    listing.append({
                        'type': item_type,
                        'name': name,
                        'size': item_size,
                        'date': item_date,
                        'path': item_path
                    })
            else:
                parser = ftpparser.FTPParser()
                data = []
                ftp.dir(data.append)
                results = parser.parse(data)
                listing = []
                for result in results:
                    name, size, timestamp, isdirectory, downloadable, islink, permissions = result
                    if name in ['.', '..']:
                        continue
                    item_type = 'directory' if isdirectory else 'file'
                    item_size = size
                    item_date = timestamp
                    item_path = f"{path}/{name}"
                    listing.append({
                        'type': item_type,
                        'name': name,
                        'size': item_size,
                        'date': item_date,
                        'path': item_path
                    })

            name_link = '.'
            size_link = '?SIZE_DESC'
            date_link = '?DATE_DESC'
            ext_link = '?EXT_ASC'

            # Sort the listing by name (case-insensitive) with directories first
            if sort_order == 'NAME_ASC':
                name_link = '?NAME_DESC'
                listing.sort(key=lambda row: (row['type'] != 'directory', row['name'].lower()))
            elif sort_order == 'NAME_DESC':
                listing.sort(key=lambda row: (row['type'] != 'directory', row['name'].lower()), reverse=True)
            elif sort_order == 'SIZE_ASC':
                listing.sort(key=lambda row: (row['type'] != 'directory', row['size']))
            elif sort_order == 'SIZE_DESC':
                size_link = '?SIZE_ASC'
                listing.sort(key=lambda row: (row['type'] != 'directory', row['size']), reverse=True)
            elif sort_order == 'DATE_ASC':
                listing.sort(key=lambda row: (row['type'] != 'directory', row['date']))
            elif sort_order == 'DATE_DESC':
                date_link = '?DATE_ASC'
                listing.sort(key=lambda row: (row['type'] != 'directory', row['date']), reverse=True)
            elif sort_order == 'EXT_ASC':
                ext_link = '?EXT_DESC'
                listing.sort(key=lambda row: (row['type'] != 'directory', os.path.splitext(row['name'])[1]))
            elif sort_order == 'EXT_DESC':
                listing.sort(key=lambda row: (row['type'] != 'directory', os.path.splitext(row['name'])[1]),
                             reverse=True)
            else:
                # Default sort order
                name_link = '?NAME_DESC'
                listing.sort(key=lambda row: (row['type'] != 'directory', row['name'].lower()))

            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(f'<html><head><title>FTP Proxy</title>{STYLES}</head><body>'.encode())
            self.wfile.write((path_to_html_links(address + path).encode()))
            self.wfile.write('<hr><table>'.encode())
            self.wfile.write(
                f'<tr><th><a href="{name_link}">Name</a></th><th><a href="{ext_link}">File type</a></th><th><a href="{size_link}">Size</a></th><th><a href="{date_link}">Date</a></th></tr>'.encode())
            if path != '':
                self.wfile.write(f'<tr><td><a href="..">..</a></td><td></td><td></td></tr>'.encode())

            for item in listing:
                size = ''
                file_type = html.escape('<dir>')
                if item['type'] == 'file':
                    size = format_size(item["size"])
                    file_type = os.path.splitext(item["name"])[1].lstrip('.')
                date = datetime.fromtimestamp(item['date']).strftime('%Y-%m-%d %H:%M') if item['date'] else ''
                new_url = generate_new_url(item["path"], item['type'], sort_order)
                self.wfile.write(
                    f'<tr><td><a href="{new_url}">{item["name"]}</a></td><td>{file_type}</td><td>{size}</td><td>{date}</td></tr>'.encode())

            self.wfile.write(f'</table>{FOOTER}</body></html>'.encode())
        except (BrokenPipeError, ConnectionResetError) as e:
            ftp.close()
            self.send_response(500)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write('Error: {}'.format(str(e)).encode())
            # raise

    def handle_file_request(self, ftp, path):
        filename = os.path.basename(path)
        mimetype, _ = mimetypes.guess_type(filename)
        if mimetype is None:
            mimetype = 'application/octet-stream'
        filesize = ftp.size(path)  # Get the size of the file

        # Check if FTP server supports partial file downloads
        ftp_features = ftp.sendcmd('FEAT')
        if 'REST STREAM' in ftp_features:
            range_header = self.headers.get('Range')
            if range_header:
                start, end = range_header.replace('bytes=', '').split('-')
                start = int(start)
                end = int(end) if end else filesize - 1
                self.send_response(206)  # Partial content
                self.send_header('Content-Range', f'bytes {start}-{end}/{filesize}')
                self.send_header('Accept-Ranges', 'bytes')  # Server allows downloading from the middle of a file
                if mimetype == 'application/octet-stream':
                    self.send_header('Content-type', 'application/octet-stream')
                    self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
                else:
                    self.send_header('Content-type', mimetype)
                self.end_headers()

                def callback(data):
                    try:
                        self.wfile.write(data)
                    except (BrokenPipeError, ConnectionResetError):
                        ftp.close()
                        # raise

                ftp.retrbinary(f'RETR {path}', callback, rest=start)
                return

        # Full file download
        self.send_response(200)
        if 'REST STREAM' in ftp_features:
            self.send_header('Accept-Ranges', 'bytes')  # Server allows downloading from the middle of a file
        else:
            self.send_header('Accept-Ranges', 'none')  # Server doesn't allow downloading from the middle of a file
        if mimetype == 'application/octet-stream':
            self.send_header('Content-type', 'application/octet-stream')
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        else:
            self.send_header('Content-type', mimetype)
        self.send_header('Content-Length', filesize)  # Send the size of the file
        self.end_headers()

        def callback(data):
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                ftp.close()
                # raise

        ftp.retrbinary(f'RETR {path}', callback, rest=0)


def format_size(size):
    for unit in ['bytes', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def generate_new_url(path, file_type, sort_order) -> str:
    if sort_order == 'NAME_ASC' or sort_order == '':
        sort_order = ''
    else:
        sort_order = '?' + sort_order
    if file_type == 'directory':
        new_url = os.path.basename(path) + '/' + sort_order
    else:
        new_url = os.path.basename(path)
    return new_url


def path_to_html_links(path: str) -> str:
    parts = path.split('/')
    links = []
    for i in range(len(parts)):
        if i == len(parts) - 1:
            links.append(f'{parts[i]}')
        else:
            links.append(f'<a href="{"../" * (len(parts) - i - 1)}">{parts[i]}</a>')
    return ' / '.join(links)


def main():
    with ThreadedTCPServer(('', PORT), FTPProxyHandler) as httpd:
        print("Server started on port", PORT)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
