import logging
import zipfile
import os
import urllib

import http_client
import version
import config

class Updater:
    def __init__(self):
        self.logger = logging.getLogger('app.' + __name__)
        self.update_flag = False
        self.auto = config.config['update']['auto_update_enabled']

    def check_for_updates(self):
        if self.new_version_available():
            self.logger.info('Updates available!')
            self.update_flag = True

    def new_version_available(self):
        if config.config['update']['enabled']:
            connection = http_client.connect(http_client.URL)
            last_version = http_client.get_request(connection, None, http_client.get_last_version_path)
            if last_version:
                reload(version)
                return self.compare_versions(version.version, last_version)

    def compare_versions(self, current_version, available_version):
        current_version = self.version_to_int(current_version)
        available_version = self.version_to_int(available_version)
        if len(current_version) == len(available_version):
            for number in range(0, len(current_version)):
                if current_version[number] < available_version[number]:
                    return True
                else:
                    continue
        else:
            self.logger.warning('Error while comparing versions!')

    def version_to_int(self, version):
        version = version.split('.')
        for number in range(0, len(version)):
            version[number] = int(version[number])
        return version

    def auto_update(self):
        if self.auto:
            self.update()

    def update(self):
        if self.update_flag:
            self.logger.info('Updating client...')
            try:
                update_file_name = config.config['update']['update_file_name']
                urllib.urlretrieve(config.config['update']['update_file_url'] + update_file_name, update_file_name)
                z = zipfile.ZipFile(update_file_name)
                z.extractall()
                z.close()
                os.remove(update_file_name)
            except Exception as e:
                error = "Update failed! " + str(e)
                self.logger.error(error, exc_info=True)
                return error
            else:
                self.logger.info('...client successfully updated!')
                self.update_flag = False


if __name__ == '__main__':
    logging.basicConfig(level='INFO')
    u = Updater()
    u.new_version_available()