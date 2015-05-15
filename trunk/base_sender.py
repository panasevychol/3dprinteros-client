import os
import base64
import thread
import logging
import collections

import log
import config
import http_client


class BaseSender:

    def __init__(self, profile, usb_info):
        self.logger = logging.getLogger('app.' + __name__)
        self.stop_flag = False
        self.profile = profile
        self.usb_info = usb_info
        self.error_code = None
        self.error_message = ''
        self.temps = [0]
        self.target_temps = [0]
        for _ in range(0, profile['extruder_count']):
            self.temps.append(0)
            self.target_temps.append(0)
        self.total_gcodes = None
        self.buffer = collections.deque()
        self.downloading_flag = False
        self.downloader = None
        self.current_line_number = 0
        self.loading_gcodes_flag = False
        self.cancel_after_loading_flag = False
        #self._position = [0.00,0.00,0.00]

    def set_total_gcodes(self, length):
        raise NotImplementedError

    def load_gcodes(self, gcodes):
        raise NotImplementedError

    def download_gcodes_and_print(self, gcodes):
        self.downloader = http_client.File_Downloader(self)
        self.downloading_flag = True
        thread.start_new_thread(self.download_thread, (gcodes,))

    def preprocess_gcodes(self, gcodes):
        gcodes = gcodes.split("\n")
        gcodes = filter(lambda item: item, gcodes)
        while gcodes[-1] in ("\n", "\r\n", "\t", " ", "", None):
            line = gcodes.pop()
            self.logger.info("Removing corrupted line '%s' from gcodes tail" % line)
        length = len(gcodes)
        self.set_total_gcodes(length)
        self.logger.info('Got %d gcodes to print.' % length)
        return gcodes

    def gcodes(self, gcodes, is_link = False):
        if is_link:
            if self.downloading_flag:
                self.logger.warning('Download command received while downloading processing. Aborting...')
                return False
            else:
                self.download_gcodes_and_print(gcodes)
        else:
            gcodes = base64.b64decode(gcodes)
            self.unbuffered_gcodes(gcodes)

    @log.log_exception
    def download_thread(self, link):
        if not self.stop_flag:
            self.logger.info('Starting download thread')
            gcode_file_name = self.downloader.async_download(link)
            if gcode_file_name:
                with open(gcode_file_name, 'rb') as f:
                    gcodes = f.read()
                try:
                    self.loading_gcodes_flag = True
                    self.load_gcodes(gcodes)  # Derived class method call, for example makerbot_sender.load_gcodes(gcodes)
                except Exception as e:
                    self.error_code = 37
                    self.error_message = "Exception occured when printrun was parsing gcodes. Corrupted gcodes? " + str(e)
                finally:
                    self.loading_gcodes_flag = False
                self.logger.info('Gcodes loaded to memory, deleting temp file')
            try:
                os.remove(gcode_file_name)
            except:
                self.logger.warning("Error while removing temporary gcodes file: " + gcode_file_name)
            self.downloader = None
            self.logger.info('Download thread has been closed')
            self.downloading_flag = False
            if self.cancel_after_loading_flag:
                self.cancel()
                self.cancel_after_loading_flag = False

    def is_downloading(self):
        return self.downloading_flag

    def cancel_download(self):
        self.downloading_flag = False
        self.logger.info("File downloading has been cancelled")
        if self.loading_gcodes_flag:
            self.logger.info("Gcodes loading in progress. Setting flag to cancel print right after load.")
            self.cancel_after_loading_flag = True

    def get_temps(self):
        return self.temps

    def get_target_temps(self):
        return self.target_temps

    def pause(self):
        self.pause_flag = True

    def unpause(self):
        self.pause_flag = False

    def close(self):
        self.stop_flag = True

    def get_error_code(self):
        return self.error_code

    def get_error_message(self):
        return self.error_message

    def is_error(self):
        return self.error_code != None

    def is_paused(self):
        return self.pause_flag

    def is_operational(self):
        return False

    def upload_logs(self):
        log.make_full_log_snapshot()
        self.logger.info("Sending logs")
        log.send_all_snapshots(config.get_app().user_login.user_token)
        self.logger.info("Done")

    def switch_camera(self, module):
        self.logger.info('Changing camera module to %s due to server request' % module)
        config.get_app().switch_camera(module)

    def update_software(self):
        self.logger.info('Executing update command from server')
        config.get_app().updater.update()

    def quit_application(self):
        self.logger.info('Received quit command from server!')
        config.get_app().stop_flag = True