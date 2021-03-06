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

# Author: Vladimir Avdeev <another.vic@yandex.ru>, Oleg Panasevych <panasevychol@gmail.com>

import os
import sys
import time
import zipfile
import logging
import logging.handlers
import traceback
import shutil

import paths
import requests
import http_client

from os.path import join

LOG_SNAPSHOT_LINES = 200

LOG_FILE = join(paths.current_settings_folder(), "3dprinteros_client.log")
EXCEPTIONS_LOG_FILE = join(paths.current_settings_folder(), 'critical_errors.log')
CLOUD_SYNC_LOG_FILE = join(paths.current_settings_folder(), '3dprinteros_cloudsync.log')
LOG_SNAPSHOTS_DIR = join(paths.current_settings_folder(), 'log_snapshots')

def create_logger(logger_name, log_file_name=None):
    logger = logging.getLogger(logger_name)
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    std_handler = logging.StreamHandler(stream=sys.stdout)
    std_handler.setLevel(logging.DEBUG)
    logger.addHandler(std_handler)
    if log_file_name:
        try:
            file_handler = logging.handlers.RotatingFileHandler(log_file_name, maxBytes=1024*1024*10, backupCount=10)
            file_handler.setFormatter(logging.Formatter('%(levelname)s\t%(asctime)s\t%(threadName)s/%(funcName)s\t%(message)s'))
            file_handler.setLevel(logging.DEBUG)
            logger.addHandler(file_handler)
            print "File logger created: " + log_file_name
        except Exception as e:
            logger.debug('Could not create log file because' + e.message + '\n.No log mode.')
    return logger

def log_exception(func_or_methon):
    def decorator(*args, **kwargs):
        try:
            result = func_or_methon(*args, **kwargs)
        except SystemExit:
            pass
        except:
            trace = traceback.format_exc()
            print trace
            with open(EXCEPTIONS_LOG_FILE, "a") as f:
                f.write(time.ctime() + "\n" + trace + "\n")
            sys.exit(0)
        else:
            return result
    return decorator

def prepare_logs_to_send():
    log_files = []
    logger = logging.getLogger("app." + __name__)
    for handler in logger.handlers:
        handler.flush()
    possible_paths = paths.get_paths_to_settings_folder()
    for path in possible_paths:
        for log in os.listdir(path):
            try:
                if log.startswith(os.path.basename(LOG_FILE))\
                        or log.startswith(os.path.basename(EXCEPTIONS_LOG_FILE))\
                        or log.startswith(os.path.basename(CLOUD_SYNC_LOG_FILE)):
                    log_files.append(os.path.join(path, log))
            except Exception:
                continue
    if not log_files:
        logger.info('Log files was not created for some reason. Nothing to send')
        return
    if not os.path.exists(LOG_SNAPSHOTS_DIR):
        try:
            os.mkdir(LOG_SNAPSHOTS_DIR)
        except Exception as e:
            logger.warning("Can't create directory %s" % LOG_SNAPSHOTS_DIR)
            return e
    for fname in log_files:
        shutil.copyfile(fname, os.path.join(LOG_SNAPSHOTS_DIR, os.path.basename(fname)))

def compress_and_send(user_token, log_file_names=None):
    if not log_file_names:
        return
    logger = logging.getLogger('app.' + __name__)
    zip_file_name = time.strftime("%Y_%m_%d___%H_%M_%S", time.localtime()) + ".zip"
    for number, name in enumerate(log_file_names):
        log_file_names[number] = os.path.abspath(os.path.join(LOG_SNAPSHOTS_DIR, name))
    zip_file_name_path = os.path.abspath(os.path.join(LOG_SNAPSHOTS_DIR, zip_file_name))
    logger.info('Creating zip file : ' + zip_file_name)
    try:
        zf = zipfile.ZipFile(zip_file_name_path, mode='w')
        for name in log_file_names:
            if not name.endswith('zip'):
                zf.write(name, os.path.basename(name), compress_type=zipfile.ZIP_DEFLATED)
        zf.close()
    except Exception as e:
        logger.warning("Error while creating logs archive " + zip_file_name)
        logger.warning('Error: ' + e.message)
    else:
        get_path = http_client.HTTPClient()
        url = 'https://' + get_path.URL + get_path.token_send_logs_path
        user_token = {'user_token': user_token}
        logger.info('Sending logs to %s' % url)
        with open(zip_file_name_path, 'rb') as f:
            files = {'file_data': f}
            print(zip_file_name_path)
            r = requests.post(url, data=user_token, files=files)
        result = r.text
        logger.info("Log sending response: " + result)
        os.remove(zip_file_name_path)
        if '"success":true' in result:
            for name in log_file_names:
                os.remove(name)
        else:
            logger.warning('Error while sending logs: %s' % result)
            return result

def send_logs(user_token):
    prepare_logs_to_send()
    try:
        snapshot_files = os.listdir(LOG_SNAPSHOTS_DIR)
    except OSError:
        logging.info("No logs snapshots to send")
    else:
        #print '\n\n%s\n\n' % str(file_name)
        error = compress_and_send(user_token, snapshot_files)
        if error:
            return error

def tail(f, lines=200):
    total_lines_wanted = lines
    BLOCK_SIZE = 1024
    f.seek(0, 2)
    block_end_byte = f.tell()
    lines_to_go = total_lines_wanted
    block_number = -1
    blocks = [] # blocks of size BLOCK_SIZE, in reverse order starting
                # from the end of the file
    while lines_to_go > 0 and block_end_byte > 0:
        if (block_end_byte - BLOCK_SIZE > 0):
            # read the last block we haven't yet read
            f.seek(block_number*BLOCK_SIZE, 2)
            blocks.append(f.read(BLOCK_SIZE))
        else:
            # file too small, start from begining
            f.seek(0,0)
            # only read what was not read
            blocks.append(f.read(block_end_byte))
        lines_found = blocks[-1].count('\n')
        lines_to_go -= lines_found
        block_end_byte -= BLOCK_SIZE
        block_number -= 1
    all_read_text = ''.join(reversed(blocks))
    return '\n'.join(all_read_text.splitlines()[-total_lines_wanted:])

def get_file_tail(file_path):
    if os.path.isfile(file_path):
        with open(file_path) as file:
            f = file.readlines()
        file_tail = []
        for line in range(-1,-100, -1):
            try:
                file_tail.append(f[line])
            except IndexError:
                break
        if file_tail:
            return file_tail