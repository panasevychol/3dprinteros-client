import re
import time
import logging
import threading
import makerbot_driver
import makerbot_serial as serial
import serial.serialutil

import base_sender

class Sender(base_sender.BaseSender):

    PAUSE_STEP_TIME = 0.5
    BUFFER_OVERFLOW_WAIT = 0.01
    IDLE_WAITING_STEP = 0.1
    TEMP_UPDATE_PERIOD = 5
    GODES_BETWEEN_READ_STATE = 100
    BUFFER_OVERFLOWS_BETWEEN_STATE_UPDATE = 20

    def __init__(self, profile, usb_info):
        base_sender.BaseSender.__init__(self, profile, usb_info)
        #self.mb = {'preheat': False, 'heat_shutdown': False}
        self.logger = logging.getLogger('app.' + __name__)
        self.logger.info('Makerbot printer created')
        self.init_target_temp_regexps()
        self.execution_lock = threading.Lock()
        self.buffer_lock = threading.Lock()
        self.parser = None
        try:
            self.parser = self.create_parser()
            time.sleep(0.1)
            self.parser.state.values["build_name"] = '3DPrinterOS'
        except Exception as e:
            self.error_code = 'No connection'
            self.error_message = str(e)
            raise RuntimeError("No connection to makerbot printer %s" % str(profile))
        else:
            self.stop_flag = False
            self.pause_flag = False
            self.printing_flag = False
            self.cancel_flag = False
            self.sending_thread = threading.Thread(target=self.send_gcodes)
            self.sending_thread.start()

    def create_parser(self):
        factory = makerbot_driver.MachineFactory()
        machine = factory.build_from_port(self.profile['COM'])
        assembler = makerbot_driver.GcodeAssembler(machine.profile)
        parser = machine.gcodeparser
        start, end, variables = assembler.assemble_recipe()
        parser.environment.update(variables)
        return parser

    def init_target_temp_regexps(self):
        self.platform_ttemp_regexp = re.compile('\s*M109\s*S(\d+)\s*T(\d+)')
        self.extruder_ttemp_regexp = re.compile('\s*M104\s*S(\d+)\s*T(\d+)')

    def append_position_and_lift_extruder(self):
        position = self.get_position()
        if position:
            with self.buffer_lock:
                self.buffer.appendleft('G1 Z' + str(position[2]) + ' A' + str(position[3]) + ' B' + str(position[4]))
            z = min(160, position[2] + 30)
            a = max(0, position[3] - 5)
            b = max(0, position[4] - 5)
            self.execute('G1  Z' + str(z) + ' A' + str(a) + ' B' + str(b))

    # length argument is used for unification with Printrun. DON'T REMOVE IT!
    def set_total_gcodes(self, length=0):
        self.execute(lambda: self.parser.s3g.abort_immediately())
        self.parser.state.values["build_name"] = '3DPrinterOS'
        self.parser.state.percentage = 0
        self.logger.info('Begin of GCodes')
        self.execute(lambda: self.parser.s3g.set_RGB_LED(255, 255, 255, 0))

    def gcodes(self, gcodes, is_link=False):
        if is_link:
            base_sender.BaseSender.gcodes(self, gcodes)
        else:
            gcodes = gcodes.split("\n")
            self.set_total_gcodes()
            for code in gcodes:
                with self.buffer_lock:
                    self.buffer.append(code)
            #with self.buffer_lock:
                #self.buffer.extend(gcodes)
            self.logger.info('Enqueued block: ' + str(len(gcodes)) + ', total: ' + str(len(self.buffer)))

    def cancel(self, go_home=True):
        with self.buffer_lock:
            self.buffer.clear()
        self.pause_flag = False
        self.cancel_flag = True
        time.sleep(0.1)
        self.execute(lambda: self.parser.s3g.abort_immediately())

    def pause(self):
        if not self.pause_flag:
            self.pause_flag = True
            time.sleep(0.1)
            self.append_position_and_lift_extruder()
            return True
        else:
            return False

    def unpause(self):
        if self.pause_flag:
            self.pause_flag = False
            return True
        else:
            return False

    def get_position(self):
        position = self.parser.state.position.ToList()
        if position[2] is None or position[3] is None or position[4] is None:
            self.logger.warning("Can't get current tool position to execute extruder lift")
            # TODO check this is real print(can cause misprints)
            # self.position = self.execute(lambda: self.parser.s3g.get_extended_position())
            return position

    def emergency_stop(self):
        self.cancel(False)

    def immediate_pause(self):
        self.execute(self.parser.s3g.pause())

    def close(self):
        self.logger.info("Makerbot sender is closing...")
        self.stop_flag = True
        if threading.current_thread() != self.sending_thread:
            self.sending_thread.join(10)
            if self.sending_thread.isAlive():
                self.logger.error("Failed to join printing thread in makerbot_printer")
        if self.parser:
            if self.parser.s3g:
                try:
                    self.parser.s3g.abort_immediately()
                except Exception:
                    pass
                time.sleep(0.1)
                self.parser.s3g.close()
        self.logger.info("...done closing makerbot sender.")

    def execute(self, command):
        buffer_overflow_counter = 0
        while not self.stop_flag:
            if buffer_overflow_counter > self.BUFFER_OVERFLOWS_BETWEEN_STATE_UPDATE:
                self.logger.info('Makerbot BufferOverflow on ' + text)
                buffer_overflow_counter = 0
                self.read_state()
            try:
                command_is_gcode = isinstance(command, str)
                self.execution_lock.acquire()
                if command_is_gcode:
                    if self.cancel_flag:
                        self.cancel_flag = False
                        break
                    text = command
                    self.printing_flag = True
                    self.parser.execute_line(command)
                    self.set_target_temps(command)
                    self.logger.debug("Executing: " + command)
                    result = None
                else:
                    text = command.__name__
                    result = command()
            except (makerbot_driver.BufferOverflowError):
                buffer_overflow_counter += 1
                time.sleep(self.BUFFER_OVERFLOW_WAIT)
            except serial.serialutil.SerialException:
                self.logger.warning("Makerbot is retrying " + text)
            except Exception as e:
                self.logger.warning("Makerbot can't continue because of: %s %s" % (str(e), e.message))
                self.error_code = 1
                self.error_message = e.message
                self.close()
                break
            else:
                return result
            finally:
                self.execution_lock.release()

    def read_state(self):
        platform_temp          = self.execute(lambda: self.parser.s3g.get_platform_temperature(1))
        platform_ttemp         = self.execute(lambda: self.parser.s3g.get_platform_target_temperature(1))
        head_temp1  = self.execute(lambda: self.parser.s3g.get_toolhead_temperature(0))
        head_temp2 = self.execute(lambda: self.parser.s3g.get_toolhead_temperature(1))
        head_ttemp1 = self.execute(lambda: self.parser.s3g.get_toolhead_target_temperature(0))
        head_ttemp2 = self.execute(lambda: self.parser.s3g.get_toolhead_target_temperature(1))
        #self.mb            = self.execute(lambda: self.parser.s3g.get_motherboard_status())
        self.temps = [platform_temp, head_temp1, head_temp2]
        self.target_temps = [platform_ttemp, head_ttemp1, head_ttemp2]
        #self.position      = self.execute(lambda: self.parser.s3g.get_extended_position())

    def reset(self):
        self.buffer.clear()
        self.execute(lambda: self.parser.s3g.reset())
        self.execute(lambda: self.parser.s3g.clear_buffer())

    def is_paused(self):
        return self.pause_flag

    def is_error(self):
        return self.error_code

    def is_operational(self):
        return not self.is_error() and self.parser and self.parser.s3g.is_open() and self.sending_thread.is_alive()

    def set_target_temps(self, command):
        result = self.platform_ttemp_regexp.match(command)
        if result:
            self.target_temps[0] = int(result.group(1))
            self.logger.info('Heating platform to ' + str(result.group(1)))
        result = self.extruder_ttemp_regexp.match(command)
        if result:
            extruder_number = int(result.group(2)) + 1
            self.target_temps[extruder_number] = int(result.group(1))
            self.logger.info('Heating toolhead ' + str(extruder_number) + ' to ' + str(result.group(1)))

    def send_gcodes(self):
        last_time = time.time()
        counter = 0
        while not self.stop_flag:
            counter += 1
            current_time = time.time()
            if (counter >= self.GODES_BETWEEN_READ_STATE) or (current_time - last_time > self.TEMP_UPDATE_PERIOD):
                counter = 0
                last_time = current_time
                self.read_state()
            if self.pause_flag:
                self.printing_flag = False
                time.sleep(self.PAUSE_STEP_TIME)
                continue
            try:
                if not self.buffer_lock.acquire(False):
                    raise RuntimeError
                command = self.buffer.popleft()
            except RuntimeError:
                time.sleep(self.IDLE_WAITING_STEP)
            except IndexError:
                self.buffer_lock.release()
                if self.execute(lambda: self.parser.s3g.is_finished()):
                    self.printing_flag = False
                time.sleep(self.IDLE_WAITING_STEP)
            else:
                self.buffer_lock.release()
                self.execute(command)
        self.logger.info("Makerbot sender: sender thread ends.")

    def is_printing(self):
        return self.printing_flag

    def get_percent(self):
        return self.parser.state.percentage




