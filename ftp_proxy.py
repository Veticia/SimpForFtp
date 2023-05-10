import http.server
import socketserver
import ftplib
import os
import urllib.parse
from urllib.parse import unquote
import ftpparser
from datetime import datetime


class FTPProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(INDEX_PAGE.encode())
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

                if path.endswith('/'):
                    path = path[:-1]  # Remove trailing slash if present

                # Check if the requested path is a file or a directory
                try:
                    ftp.cwd(path)  # Try to change the working directory to the requested path
                    is_directory = True
                except ftplib.error_perm:
                    is_directory = False

                if is_directory:
                    sort_order = parsed_url.query
                    self.handle_directory_request(ftp, path, address, username, password, sort_order)
                else:
                    self.handle_file_request(ftp, path)

            except ftplib.all_errors as e:
                self.send_response(500)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write('Error: {}'.format(str(e)).encode())

    def handle_directory_request(self, ftp, path, address, username, password, sort_order):
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

        # Sort the listing by name (case-insensitive) with directories first
        if sort_order == 'NAME_ASC':
            listing.sort(key=lambda row: (row['type'] != 'directory', row['name'].lower()))
        elif sort_order == 'NAME_DESC':
            listing.sort(key=lambda row: (row['type'] != 'directory', row['name'].lower()), reverse=True)
        elif sort_order == 'SIZE_ASC':
            listing.sort(key=lambda row: (row['type'] != 'directory', row['size']))
        elif sort_order == 'SIZE_DESC':
            listing.sort(key=lambda row: (row['type'] != 'directory', row['size']), reverse=True)
        elif sort_order == 'DATE_ASC':
            listing.sort(key=lambda row: (row['type'] != 'directory', row['date']))
        elif sort_order == 'DATE_DESC':
            listing.sort(key=lambda row: (row['type'] != 'directory', row['date']), reverse=True)
        else:
            # Default sort order
            listing.sort(key=lambda row: (row['type'] != 'directory', row['name'].lower()))

        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write('<html><head><title>FTP Proxy</title></head><body>'.encode())
        self.wfile.write('<table>'.encode())
        self.wfile.write('<tr><th>Name</th><th>Size</th><th>Date</th></tr>'.encode())
        if path != '':
            parent_path = os.path.dirname(path)
            parent_url = f'/proxy/{address}{parent_path}'.rstrip('/')
            self.wfile.write(f'<tr><td><a href="{parent_url}">..</a></td><td></td><td></td></tr>'.encode())

        for item in listing:
            size = ''
            if item['type'] == 'file':
                size = format_size(item["size"])
            date = datetime.fromtimestamp(item['date']).strftime('%Y-%m-%d %H:%M') if item['date'] else ''
            if username and password:
                new_url = f'/proxy/{username}:{password}@{address}{item["path"]}'
            elif username:
                new_url = f'/proxy/{username}@{address}{item["path"]}'
            else:
                new_url = f'/proxy/{address}{item["path"]}'
            self.wfile.write(
                f'<tr><td><a href="{new_url}">{item["name"]}</a></td><td>{size}</td><td>{date}</td></tr>'.encode())

        self.wfile.write('</table></body></html>'.encode())

    def handle_file_request(self, ftp, path):
        filename = os.path.basename(path)
        filesize = ftp.size(path)  # Get the size of the file
        self.send_response(200)
        self.send_header('Content-type', 'application/octet-stream')
        self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.send_header('Accept-Ranges',
                         'none')  # Server doesn't allow downloading from the middle of a file
        self.send_header('Content-Length', filesize)  # Send the size of the file
        self.end_headers()

        ftp.retrbinary(f'RETR {path}', lambda data: self.wfile.write(data),
                       rest=None)  # Server doesn't allow retries

def format_size(size):
    for unit in ['bytes', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def main():
    PORT = 8000

    with socketserver.TCPServer(("", PORT), FTPProxyHandler) as httpd:
        print("Server started on port", PORT)
        httpd.serve_forever()


if __name__ == "__main__":
    main()