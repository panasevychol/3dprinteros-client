#!/usr/bin/env python
# -*- coding: utf-8 -*-

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

# Author: Oleg Panasevych <panasevychol@gmail.com>

import sys
import os
import shutil
import signal
import traceback
import time
import string

from os.path import join
from subprocess import Popen, PIPE

import log
import requests
import log
import paths
paths.init_path_to_libs()
import user_login
import http_client
import config

#dirty hack to switch system default encoding to UTF-8
reload(sys)
sys.setdefaultencoding('utf-8')

class Cloudsync:

    HOME_PATH = os.path.expanduser('~')
    PATH = join(HOME_PATH, 'CloudSync')
    SENDED_PATH = join(PATH, 'Successful')
    UNSENDABLE_PATH = join(PATH, 'Failed')
    favourites_link_path = join(HOME_PATH, "links\CloudSync.lnk")
    sendto_link_path = join(HOME_PATH, "AppData\Roaming\Microsoft\Windows\SendTo\CloudSync.lnk")
    desktop_link_path = join(HOME_PATH, "desktop\CloudSync Folder.lnk")
    get_url = http_client.HTTPClient()
    URL = 'https://' + get_url.URL + get_url.cloudsync_path
    CHECK_URL = URL + '/check'
    MAX_SEND_RETRY = config.get_settings()['cloud_sync']['max_send_retry']
    CONNECTION_TIMEOUT = 6

    def __init__(self):
        self.logger = log.create_logger('cloud_sync', log.CLOUD_SYNC_LOG_FILE)
        signal.signal(signal.SIGINT, self.intercept_signal)
        signal.signal(signal.SIGTERM, self.intercept_signal)
        self.mswin = sys.platform.startswith('win')
        self.names_to_ignore = [os.path.basename(self.SENDED_PATH), os.path.basename(self.UNSENDABLE_PATH)]
        self.user_token = None
        self.error_code = None
        self.error_message = ''
        self.start()

    def intercept_signal(self, signal_code, frame):
        self.logger.info("SIGINT or SIGTERM received. Closing CloudSync Module...")
        self.stop_flag = True

    def process_error(self, error_code, error_message):
        self.error_code = error_code
        self.error_message = error_message
        self.logger.warning('Error ' + str(error_code) + ' in CloudSync. ' + error_message)

    def login(self):
        self.logger.info('CloudSync login')
        ul = user_login.UserLogin(self)
        ul.wait_for_login()
        self.user_token = ul.user_token

    def create_folders(self):
        self.logger.info('Preparing CloudSync folder: ' + self.PATH)
        paths = [self.PATH, self.SENDED_PATH, self.UNSENDABLE_PATH]
        for path in paths:
            if not os.path.exists(path):
                os.mkdir(os.path.abspath(path))

    def create_shortcuts_win(self):
        paths = [self.desktop_link_path, self.sendto_link_path, self.favourites_link_path]
        is_paths = []
        for path in paths:
            is_paths.append(os.path.exists(path))
        if any(is_paths):
            return
        for path in paths:
            Popen(['cscript', 'createLink.vbs',
                   os.path.abspath(path),
                   os.path.abspath(self.PATH),
                   os.path.abspath(join(os.getcwd(),'cloudsync.ico'))])

    def remove_shortcuts_win(self):
        os.remove(self.sendto_link_path)
        os.remove(self.favourites_link_path)
        os.remove(self.desktop_link_path)

    def enable_virtual_drive(self):
        process = Popen(['subst'], stdout = PIPE, stderr = PIPE)
        stdout, stderr = process.communicate()
        if not stdout:
            abspath = os.path.abspath(self.PATH)
            letters = 'HIJKLMNOPQRSTUVWXYZ'
            for letter in letters:
                process = Popen(['subst', letter + ':', abspath], stdout = PIPE, stderr = PIPE)
                stdout, stderr = process.communicate()
                if not stdout:
                    self.logger.info("Virtual drive enabled.")
                    break

    def disable_virtual_drive(self):
        process = Popen(['subst'], stdout = PIPE, stderr = PIPE)
        stdout, stderr = process.communicate()
        if stdout:
            stdout = stdout[0]
            process = Popen(['subst', stdout + ':', '/d'], stdout = PIPE, stderr = PIPE)
            stdout, stderr = process.communicate()
            if not stdout:
                self.logger.info("Virtual drive disabled.")

    def move_file(self, current_path, destination_folder_path):
        new_file_name = os.path.basename(current_path)
        file_name, file_ext = os.path.splitext(new_file_name)
        name_count = 1
        while os.path.exists(join(destination_folder_path, new_file_name)):
            new_file_name = file_name + "(" + str(name_count) + ")" + file_ext
            name_count += 1
        shutil.move(current_path, join(destination_folder_path, new_file_name))
        self.logger.debug('Moving ' + os.path.basename(current_path) + ' to ' + os.path.basename(destination_folder_path))

    def is_sendable(self, file_path):
        name = os.path.basename(file_path)
        if self.mswin and '?' in name:
            self.logger.warning('Wrong file name ' + name + '\n Windows is unable to operate with such names')
            self.names_to_ignore.append(name)
            return
        if os.path.isdir(file_path):
            self.logger.warning('Folders are not sendable!')
            self.move_file(file_path, self.UNSENDABLE_PATH)
            return
        for char in name:
            if not char in string.printable:
                self.logger.warning('Warning! Filename containing unicode characters are not supported by 3DPrinterOS CloudSync')
                self.move_file(file_path, self.UNSENDABLE_PATH)
                return
        return True

    def get_files_to_send(self):
        files_to_send = os.listdir(self.PATH)
        for name in self.names_to_ignore:
            files_to_send.remove(name)
        for index, name in enumerate(files_to_send):
            name = files_to_send[index] = join(self.PATH, name)
            if not self.is_sendable(name):
                files_to_send.remove(name)
        return files_to_send

    def get_file_size(self, file_path):
        file_size = os.path.getsize(file_path)
        while True:
            time.sleep(1)
            if file_size == os.path.getsize(file_path):
                break
            file_size = os.path.getsize(file_path)
        return file_size

    def get_permission_to_send(self, file_path):
        try:
            file_ext = file_path.split('.')[-1]
            file_size = self.get_file_size(file_path)
            data = {'user_token': self.user_token, 'file_ext': file_ext, 'file_size': file_size}
            result = requests.post(self.CHECK_URL, data = data, timeout = self.CONNECTION_TIMEOUT)
            if not '"result":true' in result.text:
                return result.text
        except Exception as e:
            return str(e)

    def send_file(self, file_path):
        error = self.get_permission_to_send(file_path)
        if error:
            return 'Permission to send denied: ' + error
        result = ''
        count = 1
        file = open(file_path, 'rb')
        file_name = os.path.basename(file_path)
        data = { 'user_token': self.user_token, 'file_name': file_name }
        files = { 'file': file }
        while count <= self.MAX_SEND_RETRY and not self.stop_flag:
            try:
                result = requests.post(self.URL, data = data, files = files, timeout = self.CONNECTION_TIMEOUT)
                result = str(result.text)
                if '"result":true' in result:
                    file.close()
                    return
            except Exception as e:
                result = str(e)
            self.logger.info('Retrying to send ' + os.path.basename(file_path))
            count += 1
        file.close()
        return result

    def upload(self):
        files_to_send = self.get_files_to_send()
        if files_to_send:
            error = ''
            for file_path in files_to_send:
                self.logger.info('Uploading ' + os.path.basename(file_path))
                error = self.send_file(file_path)
                if error:
                    self.logger.warning('Failed to upload ' + os.path.basename(file_path) + '. ' + error)
                    self.move_file(file_path, self.UNSENDABLE_PATH)
                else:
                    self.logger.info('Successfully uploaded: ' + os.path.basename(file_path))
                    self.move_file(file_path, self.SENDED_PATH)
            if not error:
                self.logger.info('Files successfully uploaded')

    def start(self):
        self.logger.info('CloudSync started!')
        self.stop_flag = False
        self.login()
        self.create_folders()
        if self.mswin:
            self.create_shortcuts_win()
            if config.get_settings()['cloud_sync']['virtual_drive_enabled']:
                self.enable_virtual_drive()
        self.main_loop()

    def main_loop(self):
        while not self.stop_flag:
            try:
                self.upload()
                time.sleep(3)
            except IOError:
                break
        self.quit()

    def stop(self):
        self.stop_flag = True

    def quit(self):
        if self.mswin and config.get_settings()['cloud_sync']['virtual_drive_enabled']:
            self.disable_virtual_drive()
        self.logger.info('CloudSync stopped')
        os._exit(0)

if __name__ == '__main__':
    try:
        cs = Cloudsync()
    except SystemExit:
        pass
    except:
        trace = traceback.format_exc()
        print trace
        with open(config.get_settings()['error_file'], "a") as f:
            f.write(time.ctime() + "\n" + trace + "\n")