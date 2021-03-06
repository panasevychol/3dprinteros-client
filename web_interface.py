#Copyright (c) 2015 3D Control Systems LTD

#3DPrinterOS client is free software: you can redistribute it and/or modify
#it under the terms of the GNU Affero General Public License as published by
#the Free Software Foundation, either version 3 of the License, or
#(at your option) any later version.

#3DPrinterOS client is distributed in the hope that it will be useful,
#but WITHOUT ANY WARRANTY; without even the implied warranty of
#MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#GNU Affero General Public License for more details.

#You should have received a copy of the GNU Affero General Public License
#along with 3DPrinterOS client.  If not, see <http://www.gnu.org/licenses/>.

# Author: Oleg Panasevych <panasevychol@gmail.com>, Vladimir Avdeev <another.vic@yandex.ru>

import os
import sys
import time
import urllib
import hashlib
import logging
import threading
import BaseHTTPServer
from SocketServer import ThreadingMixIn

import paths
import makerware_utils
import version
import config
import log


class WebInterfaceHandler(BaseHTTPServer.BaseHTTPRequestHandler):

    YOUR_ACCOUNT_BUTTON = config.get_settings()['web_interface']['your_account_button']
    LOCALHOST_COMMANDS = config.get_settings()['web_interface']['localhost_commands']
    URL = str(config.get_settings()['URL'])

    def setup(self):
        self.get_login_flag = False
        self.working_dir = os.path.dirname(os.path.abspath(__file__))
        self.logger = logging.getLogger('app.' + __name__)
        BaseHTTPServer.BaseHTTPRequestHandler.setup(self)
        self.request.settimeout(120)

    def address_string(self):
        host, port = self.client_address[:2]
        self.logger.debug("Incoming connection from %s:%i" % (host, port))
        return host

    def read_file(self, path_in_cwd):
        with open(os.path.join(self.working_dir, path_in_cwd), 'r') as f:
            return f.read()

    def write_with_autoreplace(self, page, response=200, headers = None):
        page = page.replace('!!!VERSION!!!', 'Client v.' + version.version + ', build ' + version.build)
        page = page.replace('3DPrinterOS', '3DPrinterOS Client v.' + version.version)
        # next command removes technical prefix from our URL if exists to display it in a correct way
        url = self.URL.replace('cli-', '')
        page = page.replace('!!!URL!!!', url)
        try:
            self.send_response(response)
            if headers:
                for keyword, value in headers.iteritems():
                    self.send_header(keyword, value)
            self.end_headers()
            self.wfile.write(page)
        except Exception as e:
            self.logger.error('Error while writing page: ' + str(e))

    def do_GET(self):        
        self.logger.info("Server GET")
        if self.LOCALHOST_COMMANDS and self.path.find('get_login') >= 0:
            self.get_login_flag = True
            self.process_login()
        elif self.LOCALHOST_COMMANDS and self.path.find('logout') >= 0:
            self.process_logout()
        elif self.LOCALHOST_COMMANDS and self.path.find('quit') >= 0:
            self.quit_main_app()
        elif self.path.find('show_logs') >=0:
            self.show_logs()
        else:
            page = self.form_main_page()
            self.write_with_autoreplace(page)

    def form_main_page(self):
        page = ''
        if self.server.app:
            if self.server.app.user_login.user_token and self.YOUR_ACCOUNT_BUTTON:
                name = 'web_interface/main_loop_form.html'
            elif self.server.app.user_login.user_token and not self.YOUR_ACCOUNT_BUTTON:
                name = 'web_interface/main_loop_form_button_off.html'
            else:
                name = 'web_interface/login.html'
            page = self.read_file(name)
            printers = self.get_printers_payload()
            page = page.replace('!!!PRINTERS!!!', printers)
            login = self.server.app.user_login.login
            if login:
                page = page.replace('!!!LOGIN!!!', login)
            if makerware_utils.get_conveyor_pid():
                page = self.read_file('web_interface/conveyor_warning.html')
            if self.server.app.rights_checker_and_waiter.waiting:
                page = self.read_file('web_interface/groups_warning.html')
            if self.server.app.updater.update_flag:
                # next command performs replace to display update button when updates available
                page = page.replace('get_updates" style="display:none"', 'get_updates"')
            if config.get_settings()['cloud_sync']['enabled']:
                # next command performs replace to display CloudSync folder opening button when enabled
                page = page.replace('open_cloudsync_folder" style="display:none"', 'open_cloudsync_folder"')
        return page

    def get_printers_payload(self):
        printers_list = []
        for pi in self.server.app.printer_interfaces:
            snr = pi.usb_info['SNR']
            if not snr:
                snr = ""
            if not getattr(pi, 'printer_profile', False):
                profile = {'alias': "", 'name': 'Awaiting profile %s:%s %s'
                                                % (pi.usb_info['PID'], pi.usb_info['VID'], snr)}
            else:
                profile = pi.printer_profile
            printer = '<b>%s</b> %s' % (profile['name'], snr)
            if not pi.printer_token:
                printer = printer + '<br>' + 'Waiting type selection from server('\
                          + '<a href="http://forum.3dprinteros.com/t/how-to-select-printer-type/143" target="_blank"><font color=blue>?</font></a>)'
            if pi.report:
                report = pi.report
                state = report['state']
                progress = ''
                if state == 'ready':
                    color = 'green'
                elif state == 'printing':
                    color = 'blue'
                    progress = ' | ' + str(report['percent']) + '%'
                elif state == 'paused':
                    color = 'orange'
                    progress = ' | ' + str(report['percent']) + '%'
                elif state == 'downloading':
                    color = 'lightblue'
                else:
                    color = 'red'
                printer = printer + ' - ' + '<font color="' + color + '">' + state + progress + '</font><br>'
                temps = report['temps']
                target_temps = report['target_temps']
                if temps and target_temps:
                    if len(temps) == 3 and len(target_temps) == 3:
                        printer = printer + 'Second Tool: ' + str(temps[2]) + '/' + str(target_temps[2]) + ' | '
                    printer = printer + 'First Tool: ' + str(temps[1]) + '/' + str(target_temps[1]) + ' | ' \
                              + 'Heated Bed: ' + str(temps[0]) + '/' + str(target_temps[0])
            printers_list.append(printer)
        printers = ''.join(map(lambda x: "<p>" + x + "</p>", printers_list))
        if not printers:
            printers = '<p><b>No printers detected</b>\
                <br>Please do a power cycle for printers\
                <br>and then ensure your printers are connected\
                <br>to power outlet and usb cord</p>'
        return printers

    def do_POST(self):
        if self.path.find('login') >= 0:
            self.process_login()
        elif self.path.find('quit') >= 0:
            self.quit_main_app()
        elif self.path.find('send_logs') >= 0:
            self.send_logs()
        elif self.path.find('logout') >= 0:
            self.process_logout()
        elif self.path.find('kill_conveyor') >= 0:
            self.kill_conveyor()
        elif self.path.find('add_user_groups') >= 0:
            self.add_user_groups()
        elif self.path.find('ignore_groups_warning') >= 0:
            self.ignore_groups_warning()
        elif self.path.find('get_updates') >= 0:
            self.get_updates()
        elif self.path.find('update_software') >= 0:
            self.update_software()
        elif self.path.find('choose_cam') >= 0:
            self.choose_cam()
        elif self.path.find('switch_cam') >= 0:
            self.switch_cam()
        elif self.path.find('open_cloudsync_folder') >= 0:
            self.open_cloudsync_folder()
        else:
            self.write_message('Not found', 0, 404)

    def open_cloudsync_folder(self):
        self.server.app.cloud_sync_controller.open_cloud_sync_folder()
        self.do_GET()

    def write_message(self, message, show_time=2, response=200):
        page = self.read_file('web_interface/message.html')
        page = page.replace('!!!MESSAGE!!!', message)
        if show_time:
            page = page.replace('!!!SHOW_TIME!!!', str(show_time))
        else:
            page = page.replace('<meta http-equiv="refresh" content="!!!SHOW_TIME!!!; url=/" />', '')
        self.write_with_autoreplace(page, response=response)

    def choose_cam(self):
        if hasattr(self.server.app, 'camera_controller'):
            modules = self.server.app.camera_controller.CAMERA_MODULES
            module_selector_html = ''
            for module in modules.keys():
                if module == self.server.app.camera_controller.current_camera_name:
                    module_selector_html += '<p><input type="radio" disabled> <font color="lightgrey" title="Current live view mode">' + module + '</font></p>'
                else:
                    module_selector_html += '<p><input type="radio" name="module" value="' + module + '"> ' + module + '</p>'
            page = open(os.path.join(self.working_dir, 'web_interface/choose_cam.html')).read()
            page = page.replace('!!!MODULES_SELECT!!!', module_selector_html)
            self.write_with_autoreplace(page)
        else:
            self.write_message('Live view feature disabled')

    def switch_cam(self):
        content_length = int(self.headers.getheader('Content-Length'))
        if content_length:
            body = self.rfile.read(content_length)
            body = body.replace("+", "%20")
            body = urllib.unquote(body).decode('utf8')
            body = body.split('module=')[-1]
            self.server.app.camera_controller.switch_camera(body)
            message = 'Live view mode switched to ' + body
        else:
            message = 'Live view mode not chosen'
        self.write_message(message)

    def get_updates(self):
        page = self.read_file('web_interface/update_software.html')
        self.write_with_autoreplace(page)

    def update_software(self):
        result = self.server.app.updater.update()
        if result:
            message = result
        else:
            message = '<p>Update successful!</p><p>Applying changes...</p>'
            self.restart_main_app()
        self.write_message(message)

    def show_logs(self):
        log_file = log.LOG_FILE
        logs = log.get_file_tail(log_file)
        content = ''
        for line in logs:
            content = content + line + '<br>'
        if not content:
            content = 'No logs'
        page = self.read_file('web_interface/show_logs.html')
        page = page.replace('!!!LOGS!!!', content)
        self.write_with_autoreplace(page)

    def ignore_groups_warning(self):
        self.server.app.rights_checker_and_waiter.waiting = False
        self.do_GET()

    def add_user_groups(self):
        self.server.app.rights_checker_and_waiter.add_user_groups()
        self.quit_main_app()

    def kill_conveyor(self):        
        result = makerware_utils.kill_existing_conveyor()
        if result:
            message = 'Conveyor was successfully stopped.<br><br>Returning...'
        else:
            message = '3DPrinterOS was unable to stop conveyor.'        
        self.write_message(message)

    def send_logs(self):
        error = log.send_logs(self.server.app.user_login.user_token)
        if not error:
            message = 'Logs successfully sent'
        else:
            message = 'Error while sending logs'
        self.write_message(message)

    def quit_main_app(self):
        self.write_message('Goodbye :-)', 0)
        self.server.app.stop_flag = True

    def answer_with_image(self, img_path_in_cwd):
        #this function performs hack that necessary for sending requests from HTTPS to HTTP
        #answering with image is the only way to communicate by requests between HTTPS and HTTP
        image_path = os.path.join(os.getcwd(), img_path_in_cwd)
        with open(image_path, 'rb') as f:
            message = f.read()
        self.write_with_autoreplace(message, headers = { 'Content-Type': 'image/jpeg' })

    def process_login(self):
        if self.server.app and hasattr(self.server.app, "user_login"):
            if self.server.app.user_login.user_token:
                if self.get_login_flag:
                    self.answer_with_image('web_interface/fail.jpg')
                else:
                    self.write_message('Please logout first before re-login')
                return
        body = ''
        if self.get_login_flag:
            body = str(self.path)
            body = body.replace('/?get_', '')
            body = body.split('&nocache=')[0]
        content_length = self.headers.getheader('Content-Length')
        if content_length:
            length = int(content_length)
            body = self.rfile.read(length)
        body = body.replace("+", "%20")
        body = urllib.unquote(body).decode('utf8')
        raw_login, password = body.split("&password=")
        login = raw_login.replace("login=", "")
        password = hashlib.sha256(password).hexdigest()
        while not hasattr(self.server.app, 'user_login'):
            if not self.server.app or self.server.app.stop_flag:
                self.answer_with_image('web_interface/fail.jpg')
                return
            time.sleep(0.01)
        error = self.server.app.user_login.login_as_user(login, password)
        if error:
            message = str(error[1])
        else:
            if self.get_login_flag:
                self.answer_with_image('web_interface/success.jpg')
                return
            message = 'Login successful!<br><br>Processing...'
        self.write_message(message)

    def restart_main_app(self):
        self.server.app.set_reboot_flag(True)
        self.server.app.stop_flag = True

    def process_logout(self):
        for path in paths.get_paths_to_settings_folder():
            login_info_path = os.path.join(path, 'login_info.bin')
            if os.path.isfile(login_info_path):
                try:
                    os.remove(login_info_path)
                except Exception as e:
                    self.logger.error('Failed to logout: ' + e.message)
        if sys.platform.startswith('darwin'):
            page = self.read_file('web_interface/logout.html')
            self.write_with_autoreplace(page)
            return
        self.restart_main_app()
        self.write_message('Logout. Please wait...', show_time=4)


class ThreadedHTTPServer(ThreadingMixIn, BaseHTTPServer.HTTPServer):
    """ This class allows to handle requests in separated threads.
        No further content needed, don't touch this. """


class WebInterface(threading.Thread):
    def __init__(self, app):
        self.logger = logging.getLogger('app.' + __name__)
        self.app = app
        self.server = None
        threading.Thread.__init__(self)

    def run(self):
        self.logger.info("Starting web server...")
        try:
            self.server = ThreadedHTTPServer(("127.0.0.1", 8008), WebInterfaceHandler)
        except Exception as e:
            self.logger.error(e)
        else:
            self.logger.info("...web server started")
            self.server.app = self.app
            self.server.token_was_reset_flag = False
            self.server.serve_forever()
            self.server.app = None
            self.app = None
            self.logger.info("Web server stop.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    class A:
        pass
    a = A()
    w = WebInterface(a)