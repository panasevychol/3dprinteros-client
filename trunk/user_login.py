import time
import logging

import utils
import http_client

class UserLogin:

    def __init__(self, parent_obj):
        self.logger = logging.getLogger("app." + __name__)
        self.parent = parent_obj
        self.user_token = None
        login, password = utils.read_login()
        if login:
            self.login_as_user(login, password)

    def login_as_user(self, login, password):
        answer = http_client.send(http_client.package_user_login, (login, password, http_client.MACADDR))
        if answer:
            user_token = answer.get('user_token', None)
            error = answer['error']
            if user_token and not error:
                self.user_token = login
                if utils.write_token(login, password):
                    return
            else:
                self.logger.warning("Error processing user_login " + str(error))
                return error['code'], error['message']

        self.logger.error("Login rejected")

    def wait_for_login(self):
        self.logger.debug("Waiting for correct user login...")
        while not self.user_token or self.parent.stop_flag:
            time.sleep(0.1)
            if getattr(self.parent, "quit_flag", False):
                self.parent.quit()
        self.logger.debug("...end waiting for user login.")