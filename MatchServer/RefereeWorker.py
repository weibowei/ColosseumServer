# coding=utf-8
import json
import psutil
import threading
import subprocess
import queue
from multiprocessing import Pool
from .utils import InitSubmissionEnv
from .configure import MATCH_LOG_FILE_BASE_DIR
DEBUG_MATCH_INFO_EXAMPLE = {
    'gameID': '10112323123',  # just ID number
    'game': 'dealer_renju',  # name of the game
    'NPC': 'starter',  # 'False' 'starter' 'master' 'Godlike'
    'rounds': '1000',
    'random_seed': 'False',  # 'False' 'int'
    'if_poker': 'False',  # Special in poker game
    'game_define': 'False',  # for poker game define file is required
    'players': [
        {'name_1': 'Alice'},
        {'name_2': 'Bob'}
    ]
}
STATUS_LIST = ['waiting', 'ongoing', 'success', 'failed']


def _start_one_match(instance, task_info):
    return instance.start_one_match(task_info)


class JobThread(threading.Thread):

    def __init__(self, queue, cmd, game_id):

        """
        工作线程
        开启一个临时的文件路径存放log file
        开启游戏进程，并利用PIPE获得游戏打开传出的端口号并保存后notify
        最后读取log file中的结果保存
        临时文件删除
        :param queue:
        :param cmd: 命令行LIST
        :param game_id: 对局ID
        :return:
        """
        threading.Thread.__init__(self)
        self._queue = queue
        self.cmd = cmd
        self.game_id = game_id
        self.status = 'pending'

    def from_match_log(self, log_path):
        """
        作文件操作，从log文件读取对局得分
        :param log_path:
        :return: 对局得分String
        """
        result = 'not finished yet!'
        if self.status is STATUS_LIST[2]:
            file = open(log_path)
            lines = file.readlines()
            result = lines[-1]
        return result

    def run(self):
        print('trying to create worker thread! ')
        with InitSubmissionEnv(MATCH_LOG_FILE_BASE_DIR, self.game_id) as dir:
            p = subprocess.Popen(self.cmd, shell=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            first = False
            print('start working')
            while p.poll() is None:
                line = p.stdout.readline()
                print(line)
                if not first:
                    first = True
                    line = line.decode('utf-8')
                    ports = line.strip().split()
                    print(ports)
                    print('notify condition from game process')
                    self._queue.put(ports)
            if p.returncode == 0:
                self.status = STATUS_LIST[2]
            else:
                self.status = STATUS_LIST[3]
            self.from_match_log(dir)
        print('thread exiting')


class Task(object):
    """
    完成单个对局进程
    保存对局信息
    focus on one specify task
    """

    def __init__(self, match_info):
        """
        这里主要是一个线程的Condition初始化
        :param match_info:对局信息例子见DEBUG_MATCH_INFO_EXAMPLE
        """
        self.status = STATUS_LIST[0]
        self.log_path = MATCH_LOG_FILE_BASE_DIR
        self._queue = queue.Queue()
        self.worker = None
        self.game_id = match_info['game_id']
        self.cmd, self.log_path = self._prepare_for_work(match_info)
        print('task init!!!')

    def run(self):
        """
        开启游戏线程，并利用线程Condition完成异步同步实现端口List的获取
        :return:端口List
        """
        print('creating work thread')
        self.worker = JobThread(self._queue, cmd=self.cmd, game_id=self.game_id)
        self.worker.start()
        ports = self._queue.get()
        print('return ports')
        print(ports)
        return ports

    @staticmethod
    def _prepare_for_work(match_info):
        """
        完成将对局信息转化成命令行，同时返回log文件的路径避免重复计算
        :param match_info:对局信息例子见DEBUG_MATCH_INFO_EXAMPLE
        :return:命令行，路径
        """
        # shell_cmd = './dealer_renju asdddd 1000 Alice Bob'
        # cmd = shlex.split(shell_cmd)
        cmd = []
        print(match_info)
        log_path = MATCH_LOG_FILE_BASE_DIR + match_info['gameID'] + '/' + match_info['game']
        cmd.append(match_info['game'])
        cmd.append(log_path)
        if match_info['if_poker'] != 'False':
            print('checking bool in prepare')
            print(match_info['if_poker'] is not 'False')
            print(match_info['if_poker'] == 'False')
            cmd.append(match_info['game_define'])
        cmd.append(match_info['rounds'])
        if match_info['random_seed'] != 'False':
            cmd.append(match_info['random_seed'])
        players = match_info['players']
        for player in players:
            for key, val in player.items():
                cmd.append(val)
        print("creating cmd:")
        print(cmd)
        return cmd, log_path

    def get_status(self):
        return self.worker.status


REFEREE_RET_EXAMPLE = {
    'status': 'success',  # Task.STATUS  and   'game no exist'
    'score': 'SCORE:47|53:a|b'  # False if game is no success
}


class RefereeWorker(object):
    """
    focus on task scheduling
    每个Server实例有一个Worker对象
    用于对局线程的开启
    """

    def __init__(self):
        """
        开启进程池，由于每一个游戏实际使用的CPU资源并不高，所以并发量设置在了CPU核数*100
        以及一个Task队列，这个队列是保存了gameID:task的字典
        便于获取游戏的结果
        """
        self._pool = Pool(psutil.cpu_count()*100)
        self.task_queue = dict()

    @staticmethod
    def start_one_match(task_info):
        """
        完成一次对局
        开启对局后，马上返回游戏的端口号供用户连接或者是预置AI
        :param task_info: 开启游戏，需要的信息，例子：DEBUG_JSON
        :return: 返回端口List，任务对象
        """
        task = Task()
        port = task.run(task_info)
        print("Finishing start_one_match",port)
        return port, task

    def query_task_result(self, task_info):
        """
        使用查询方式，在网页上返回对局信息
        :param task_info: 同样是一个字典，必要的信息是gameID
        :return:如果游戏完成，返回得分，否则返回游戏当前的状态，score为False
        example:result = {'status': 'success', 'score': 'False'}
        """
        game_id = task_info['gameID']
        try:
            task = self.task_queue[game_id]
        except KeyError:
            return {
                'status': 'no exist',
                'score': 'False'
            }
        result = dict()
        result['status'] = task.get_status()
        if result['status'] is STATUS_LIST[2]:
            result['score'] = task.result
            del self.task_queue[game_id]
        else:
            result['score'] = 'False'
        return json.dumps(result)

    def accept_task(self, task_info):
        """
        在Worker中接受开启游戏对局进程的请求
        在游戏对局字典中保存task实例，以备查询需求
        完成对局后返回开启的端口号字典
        :param task_info: 开启游戏，需要的信息，例子：DEBUG_JSON
        :return: example: result = {'status': 'ongoing', 'ports': { 'player1_port': '12311', 'player2_port': '12222' }}
        """
        result = self._pool.apply_async(self.start_one_match, args=(task_info,))
        result.wait()
        port, task = result.get()
        self.task_queue[task_info['gameID']] = task
        result = dict()
        ports = dict()
        result['status'] = task.get_status()
        for i in range(len(port)):
            ports['player%d_port' % i] = port[i]
        result['ports'] = ports
        return json.dumps(result)
