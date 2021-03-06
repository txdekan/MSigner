# -*- coding:utf-8
"""
MySQL Database Connection.
change to fit for poolo class.
last update: 20171229.
update: com_con,增加flexiable模式的pool

20180103: com_con, __enter__ with elock(thread.Lock) for thread safe
201803：new arg for execute_db: get_ins_id(false), weather return last insert id
"""
import mysql.connector as mcr
import mysql.connector.pooling as mcrp
import logging
import time
#   for com_con
from random import random as rdm
from threading import Lock
logging.basicConfig(level=logging.DEBUG, format='(%(funcName)-10s) %(message)s')


class server_info:
    server_info = {
        'host': '120.77.244.198',
        'port': 3306,
        'user': 'examer',
        'password': 'exm@runner!',
        'db': 'exams',
        'charset': 'utf8',
        'connection_timeout': 3,
    }

    def __init__(self, **configure):
        self.D = dict()
        for _ in self.__class__.server_info.iterkeys():
            self.D[_] = configure.get(_) or self.__class__.server_info[_]

    def __getitem__(self, item):
        return self.D.get(item)

    def __setitem__(self, n, v):
        if n in self.D:
            self.D[n] = v

    @property
    def info(self):
        return str(self.D)


class com_con(object):
    length = 10
    dead_len = 30
    recover_time = 60
    # 120s not use pool, connections are disconnected from server
    # after testing: 953s is ok...// 1346s ok// 2047s ok
    sleeptime = 240
    #   w is a mark for pool
    w = 'pool'

    @classmethod
    def set_deadlen(cls, dlen=0):
        if dlen > cls.dead_len:
            cls.dead_len = dlen
            return dlen
        else:
            return cls.dead_len

    #   -1: not inited; 1: working; 0:shutdown
    def __init__(self, poolname, con_info, length=0, atonce=True, flexible=False, debug=False, **kwargs):
        # if flexible, work with lenth and dead_length, and self.c->last time work mode, if add new con is set to 1, if kick set to -1: 
        # when take: if overlen create new con, if over deadlen error
        # when kick: if finger > len and last time is kick, will not append to conlist for reuse, just remove it(on the other hand, if the last time action is still create new con, which means the pool may still works under busy mode)
        self.length = length or self.__class__.length
        if flexible:
            if self.length < self.__class__.dead_len:
                self.dead_len = self.__class__.dead_len
                self.__take_kick = self.__take_kick_2
                self.c = 0
            else:
                logging.warn('length is bigger than dead_len, will not work in flexible mode!')
                self.dead_len = 0
                self.__take_kick = self.__take_kick_1
        else:
            self.dead_len = 0
            self.__take_kick = self.__take_kick_1
        self.cif = con_info if isinstance(con_info, dict) else con_info.D if isinstance(con_info, server_info) else None
        if self.cif is None:
            raise RuntimeError("NO connection info!")
        self.debug = debug
        if self.debug:
            logging.info("COM_CON[%s]: debug on!" % poolname)
        self.name = poolname
        self.finger = 0
        self.aqrs = 0
        self.ilock = Lock()
        self.elock = Lock()
        self.status = -1
        self.conlist = []
        self.staticlist = []
        self.curcon = None
        self.recover_time = self.__class__.recover_time
        # too long no using
        self.sleeptime = kwargs.get('sleeptime', self.__class__.sleeptime)
        self.lasttime = time.time()
        self.w = 'pool'
        if not atonce:
            return
        self.ilock.acquire()
        try:
            self.__inilist()
        finally:
            self.ilock.release()

    def __getitem__(self, sqlcmds):
        ix = sqlcmds.find(';')
        if ix > 0 and len(sqlcmds) > ix + 1:
            return self.do_sequence(sqlcmds)
        else:
            if sqlcmds[:sqlcmds.find(' ')].lower() in ('select', 'show', 'desc'):
                rt = self.query_db(sqlcmds)
                return rt[0] if rt and len(rt) == 1 else rt
            else:
                return self.execute_db(sqlcmds)

    def __testing(self):
        try:
            con = mcr.connect(**self.cif)
            logging.debug(con.is_connected())
        except:
            logging.debug(str(self.cif))
            return False
        return con

    # when use sleep time
    def __takecon(self):
        t = time.time()
        con = self.conlist.pop(0)
        try:
            con.ping(reconnect=True, delay=0)
            #con.ping(reconnect=False, attemps=1, delay=0) #it works well when local connection, maybe the connect delay likes 0; and mysql5.5+
        except:
            logging.error("CON: %s :this con is dead with ping!" % con.connection_id)
            self.staticlist.remove(con)
            con = mcr.connect(**self.cif)
            con.mark = 0
            logging.debug("new con: %s created!" % con.connection_id)
            self.staticlist.append(con)
        self.lasttime = t
        return con

    # when use sleep time
    def __takecon2(self):
        con = self.conlist.pop(0)
        if not con.is_connected():
            logging.error("CON: %s :this con is dead with ping!" % con.connection_id)
            self.staticlist.remove(con)
        try:
            con = mcr.connect(**self.cif)
            con.mark = 0
            logging.debug("new con: %s created!" % con.connection_id)
            self.staticlist.append(con)
            self.lasttime = time.time()
        except:
            con = None
            logging.error("NOT ABLE to Create a new connection!")
        return con

    def __inilist(self):
        if self.status > 0:
            if self.length > len(self.staticlist):
                for x in range(self.length - len(self.staticlist)):
                    con = mcr.connect(**self.cif)
                    self.staticlist.append(con)
                    self.conlist.append(con)
            return
        if self.status == 0:
            if len(self.conlist) > 0:
                for i in range(len(self.conlist)):
                    self.conlist.pop()
            if len(self.staticlist) > 0:
                for i in range(len(self.staticlist)):
                    self.staticlist.pop().close()
        tcon = self.__testing()
        if tcon is False:
            logging.error("cannot connect to mysql server!")
            return -1
        self.staticlist = [None] * self.length
        tcon.mark = 0
        self.staticlist[0] = tcon
        self.conlist.append(tcon)
        for i in range(1, self.length):
            con = mcr.connect(**self.cif)
            con.mark = 0
            self.conlist.append(con)
            self.staticlist[i] = con
            time.sleep(0.05)
        logging.debug("INITIAL POOL DONE!")
        self.status = 1
        return self.status

    def __fix_cons(self, con=None):
        try:
            checkcon = mcr.connect(**self.cif)
            cur = checkon.cursor()
            cur.execute("show processlist")
        except:
            cur.close()
            checkcon.close()
            return False
        dbrt = cur.fetchall()
        if not dbrt:
            return False
        for con_stat in dbrt:
            # con_stat: id:0, User:1, Host:2, db: 3, Command:4, Time:5
            if con_stat[3] == 'Query' and con_stat[5] > 30:
                #long query
                cur.execute("kill QUERY %s" % con_stat[0])
                logging.info("reset a long query connect: %s" % con_stat[0])
                for _ in range(len(self.conlist)):
                    if _.connection_id == con_stat[0]:
                        self.conlist.remove(_)
                        self.staticlist.remove(_)
                        break
        cur.close()
        checkcon.close()
        return True

    def resetcon(self, con):
        loger.warn("a con to be reset！")
        try:
            con.close()
        except:
            pass
        finally:
            con = mcr.connect(**self.cif)
        return con

    def __batch_recovery(self):
        logging.warn("on __batch_recovery!")
        for _ in range(len(self.conlist)):
            self.conlist.pop()
        for _ in range(len(self.staticlist)):
            con = self.staticlist.pop()
            if not con.is_connected():
                del con
                continue
            if con.unread_result:
                con.get_rows()
            con.mark = 0
            self.conlist.push(con)
        for _ in range(self.length - len(self.conlist)):
            self.conlist.append(mcr.connect(**self.cif))
            time.sleep(0.05)
        for _ in self.conlist:
            self.staticlist.append(_)
        self.finger = 0
        self.length = len(self.conlist)
        return self.finger

    def __str__(self):
        return 'pool status: %s\tfinger: %s\t; usage: %s/%s' % (self.status, self.finger, len(self.conlist), len(self.staticlist))

    __repr__ = __str__

    def __take_kick_2(self, con=None):
        logging.debug('con_pool=>status: %s\tfinger: %s\t; usage: %s/%s' % (self.status, self.finger, len(self.conlist), len(self.staticlist)))
        #   work on flexible mode
        def newcon():
            ncon = mcr.connect(**self.cif)
            if ncon:
                # create con and direct use, so it's no need to append to conlist
                self.staticlist.append(ncon)
                self.finger += 1
                self.c = 1
                return ncon
            else:
                return None

        if self.aqrs == 1:
            time.sleep(3)
            self.ilock.release()
        self.ilock.acquire()
        self.aqrs = 1
        if con:
            if self.finger > self.length and self.c < 0:
                self.staticlist.remove(con)
                del con
            else:
                self.conlist.append(con)
                con.mark = 0
            self.c = -1
            self.finger -= 1
            return self.finger
        if self.status == 0:
            if self.__inilist() != 1:
                self.ilock.release()
                self.aqrs = 0
                raise RuntimeError('Cannot Initial the Pool!')
        elif self.status == -1:
            self.conlist = []
            self.staticlist = []
            con = newcon()
            self.ilock.release()
            self.aqrs = 0
            if con:
                self.status = 1
                return con
            else:
                return RuntimeError('Not able to inital the pool!')
        if self.finger >= self.dead_len:
            if self.__batch_recovery() >= self.dead_len:
                self.ilock.release()
                self.aqrs = 0
                raise RuntimeError('Work on flexible Mode and over dead_len!')
        elif self.finger >= self.length:
            con = newcon()
            self.ilock.release()
            self.aqrs = 0
            return con
        con = self.__takecon()
        self.finger += 1
        self.c = 1
        self.aqrs = 0
        self.ilock.release()
        return con

    def __take_kick_1(self, con=None):
        if con:
            self.conlist.append(con)
            con.mark = 0
            self.finger -= 1
            logging.debug("release con: %s" % str(self))
            return self.finger
        logging.debug('conpool=>status: %s\tfinger: %s\t; usage: %s/%s' % (self.status, self.finger, len(self.conlist), len(self.staticlist)))
        self.ilock.acquire()
        if self.status <= 0:
            self.__inilist()
            if self.status != 1:
                logging.warn('Initial con list Error!')
                self.ilock.release()
                raise RuntimeError('Cannot Initial the Pool!')
            self.finger = 0
        if self.finger >= self.length:
            try:
                self.__batch_recovery()
            except:
                self.status = 0
                self.ilock.release()
                return None
            if self.finger >= self.length - 1:
                if self.__fix_cons() is False:
                    self.ilock.release()
                    return None
        con = self.__takecon()
        self.finger += 1
        if self.ilock.locked():
            self.ilock.release()
        return con

    def __enter__(self):
        self.elock.acquire()
        if self.curcon is None:
            logging.info('con for with is still None, create it!')
            self.curcon = mcr.connect(self.cif)
        else:
            self.curcon.ready = 1
        return self.curcon

    def __exit__(self, exc_type, exc_val, exc_tb):
        print(exc_val)
        self.elock.release()
        self.curcon.ready = 0
        return True

    def shutdown(self):
        self.ilock.acquire()
        for _ in range(len(self.staticlist)):
            con = self.staticlist.pop()
            con.close()
            logging.info("shutdown connection %s" % con.connection_id)
        self.status = 0
        self.ilock.release()

    def free(self):
        con = self.__take_kick()
        if con:
            con.mark = int(time.time())
            return con
        else:
            return None

    def release(self, con=None):
        if con:
            if con.unread_result:
                logging.debug("release a con with unread_result")
                con.get_rows()
            return self.__take_kick(con)
        else:
            return self.__batch_recovery()

    def execute_db(self, cmd, get_ins_id=False):
        con = self.__take_kick()
        if con is None:
            logging.warn("no connection taken!")
            return None
        if self.debug:
            logging.debug(cmd)
        try:
            cur = con.cursor()
            cur.execute(cmd)
            if get_ins_id:
                rt = cur.lastrowid
            else:
                rt = cur.rowcount
            cur.close()
        except Exception as e:
            print(e)
            if not con.is_connected():
                print("con is not connected!")
                #self.staticlist.remove(con)
                con = self.resetcon(con)
            rt = False
        self.__take_kick(con)
        return rt

    def us_execute_db(self, sqlcmd):
        con = self.__take_kick()
        if con is None:
            logging.warn("no connection taken!")
            return None
        logging.debug(sqlcmd)
        rt = None
        cur = con.cursor()
        #cmd = ';'.join(('SET SQL_SAFE_UPDATES=0', sqlcmd, ''))
        try:
            #cur.execute(cmd, multi=True)
            cur.execute('SET SQL_SAFE_UPDATES=0;')
            cur.execute(sqlcmd)
            rt = cur.rowcount   # rt could be 0 as no delete
        except:
            rt = False
        finally:
            cur.execute('SET SQL_SAFE_UPDATES=1;')
        cur.close()
        self.__take_kick(con)
        return rt

    def query_db(self, cmd, one=False, single=False):
        # if one then ignore single
        # single: ((1,), (2,), (3,), (4,), ...) => [1,2,3,4, ...]
        con = self.__take_kick()
        if con is None:
            logging.warn("no connection taken!")
            return None
        if self.debug:
            logging.debug(cmd)
        try:
            cur = con.cursor()
            cur.execute(cmd)
            rt = cur.fetchall()
        except mcr.Error as err:
            logging.error("sql error: %s: %s" % (err.errno, err.msg))
            if not con.is_connected():
                con.close()
                self.staticlist.remove(con)
            else:
                self.__take_kick(con)
            rt = None
        else:
            if con.unread_result:
                con.get_rows()
            self.__take_kick(con)
            cur.close()
        if rt:
            if one:
                rt = rt[0]
            elif single:
                rt = [_[0] for _ in rt]
        return rt

    def do_sequence(self, sql_seq, ignore=False):
        con = self.__take_kick()
        cur = con.cursor()
        dbrt_count = 0
        sql_seq = sql_seq if isinstance(sql_seq, list) else sql_seq.split(";")
        #cur.execute(sql_seq, multi=True)
        for sql in sql_seq:
            try:
                result = cur.execute(sql)
                if not result:
                    continue
                elif result.with_rows:
                    result.fetchall()
                dbrt_count += 1
            except Exception as E:
                logging.error("error with: %s" % E)
                pass
        cur.close()
        self.__take_kick(con)
        return dbrt_count


class com_con2(object):
    w = 'pool'
    length = 10

    def __init__(self, poolname, server_args, length=0, **extargs):
        self.server = server_args if isinstance(server_args, dict) else server_args.D if isinstance(server_args, server_info) else None
        assert self.server
        self.pool = mcrp.MySQLConnectionPool(pool_name=poolname, pool_size=length or self.__class__.size, **self.server)
        if not self.pool:
            raise RuntimeError("Not Able to Create a connect pool for mysql!")
        self.lastcmd = ''
        self.curcon = None

    def __getitem__(self, cmds):
        if isinstance(cmds, str) :
            operation = cmds.split(' ')[0]
            if operation.lower() in ('insert', 'update', 'delete'):
                return self.execute_db(cmds)
            else:
                rt = self.query_db(cmds)
                return rt[0] if rt and len(rt) == 1 else rt
        else:
            return self.execute_dbs(cmds)

    def shutdown(self):
        return True

    def reset(self, server_args):
        self.server = server_args
        self.pool.set_config(**server)

    def free(self):
        con = self.pool.get_connection()
        return con if con.is_connected() else None

    def release(self, con=None):
        return True

    def __enter__(self):
        self.elock.acquire()
        if self.curcon is None:
            logging.info('con for with is still None, create it!')
            self.curcon = self.pool.get_connection()
        return self.curcon

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.curcon.close()
        self.elock.release()

    def execute_db(self, cmd, get_ins_id=False):
        con = self.pool.get_connection()
        if con:
            cur = con.cursor()
            cur.execute(cmd)
            if cur.fetchone() and get_ins_id:
                cur.execute('SELECT LAST_INSERT_ID;')
                rt = cur.fetchone()[0]
            else:
                rt = True
            cur.close()
            con.close()
            return rt
        raise RuntimeError('NO connection to take, with current finger=%s' % self.finger)

    def execute_dbs(self, cmds):
        cmdstr = ';'.join(cmds) if isinstance(cmds, (tuple, list)) else cmds
        con = self.pool.get_connection()
        cur = con.cursor(multi=True)
        dbrt = []
        for result in cur.execute(operation, multi=True):
            if result.with_rows:
                dbrt.append(result.fetchall())
            else:
                dbrt.append(result.rowcount)
        cur.close()
        con.close()
        return dbrt

    def us_execute_db(self, sqlcmd):
        con = self.pool.get_connection()
        if con:
            cur = con.cursor()
            cur.execute('SET SQL_SAFE_UPDATES=0;')
            cur.execute(cmd)
            if cur.fetchone() and get_ins_id:
                cur.execute('SELECT LAST_INSERT_ID;')
                rt = cur.fetchone()[0]
            cur.execute('SET SQL_SAFE_UPDATES=0;')
            cur.close()
            con.close()
            return rt
        raise RuntimeError('NO connection to take')     

    def query_db(self, cmd, one=False):
        con = self.pool.get_connection()
        if con:
            cur = con.cursor()
            cur.execute(cmd)
            if one:
                rt = cur.fetchone()[0]
            else:
                rt = cur.fetchall()
            cur.close()
            con.close()
            return rt
        raise RuntimeError('NO connection to take')


class rwcon(com_con):

    def __init__(self, wcon_conf, rcon_conf, rlen, ratonce=False):
        self.wconf = wcon_conf
        super(rwcon, self).__init__('rcons', rcon_conf, length=rlen, atonce=ratonce, flexible=False)

    def execute_db(self, cmd, get_ins_id=False):
        con = mcr.connect(**self.wcon_conf)
        if con:
            cur = con.cursor()
            logging.debug(cmd)
            rlt = cur.execute(cmd)
            if get_ins_id:
                rt = cur.lastrowid
            else:
                rt = cur.rowcount
            cur.close()
            con.close()
            return rt
        raise RuntimeError('NO connection to write server!')

    def us_execute_db(self, sqlcmd):
        con = mcr.connect(**self.wcon_conf)
        if con:
            cur = con.cursor()
            cmd = ';'.join(('SET SQL_SAFE_UPDATES=0', sqlcmd, 'SET SQL_SAFE_UPDATES=1'))
            cur.execute(cmd)
            cur.close()
            con.close()
            return 1
        raise RuntimeError('NO connection to write server!')


if __name__ == '__main__':
    #server = server_info().D
    server = {'host': 'localhost', 'user': 'test1', 'db':'exams', 'password': '123456'}
    testpool = com_con('testing', server, length=3)
    dbrt = testpool['select * from user']
    print(dbrt)
    # testdbc = mdb_mysql(server)
    # if testdbc.connect_db() == 1:
    #     print 'YES'
    # else:
    #     print 'NO'
    # print testdbc['show tables;']
    # testdbc.close()
