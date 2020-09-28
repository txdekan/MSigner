#!/usr/bin/env python
# -*- coding: utf8

import sys
import urllib
import http.client as httplib
import hmac
from hashlib import sha1
from urllib.parse import quote
#import logging
import json
import re
import time
from threading import Timer
from .message_sets import appmsg_sets, appmsg_sets2
from sys_config import cur_config, loger
from .campusApp2 import thirdsvr
from ultilities import json_flat_2_tree

# 适配智慧校园2.0版本
# PYTHON3 ON Linux：
if sys.platform == 'linux':
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context
    #Context = ssl.create_default_context()
    #Context = ssl._create_unverified_context()
else:
    Context = None
"""
https://www.cnblogs.com/lykbk/p/ASDFQAWQWEQWEQWEQWEQWEQWEQEWEQW.html
问题的原因是“SSL: CERTIFICATE_VERIFY_FAILED”。
Python 升级到 2.7.9 之后引入了一个新特性，当使用urllib.urlopen打开一个 https 链接时，会验证一次 SSL 证书。
而当目标网站使用的是自签名的证书时就会抛出一个 urllib2.URLError: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed (_ssl.c:581)> 的错误消息，

解决方案包括下列两种方式：

1. 使用ssl创建未经验证的上下文，在urlopen中传入上下文参数

import ssl
import urllib2

context = ssl._create_unverified_context()
print urllib2.urlopen("https://www.12306.cn/mormhweb/", context=context).read()
2. 全局取消证书验证

import ssl
import urllib2
 
ssl._create_default_https_context = ssl._create_unverified_context
 
print urllib2.urlopen("https://www.12306.cn/mormhweb/").read()
注意：在全全局请求文件导入import ssl

import ssl
ssl._create_default_https_context = ssl._create_unverified_context
"""
#loger = logging.getLogger()
#loger.basicConfig(level=logging.DEBUG, format='(%(funcName)-10s) %(message)s')


# 通知发送类
# TODO: 增加消息模版功能
class pmsgr(object):
    msgtypes = ['text', 'image', 'textcard', 'news']

    @classmethod
    def from_request(cls, reqdata, server=None):
        # directly from http request data generate a message
        mtype = reqdata.get('msgtype', 'text')
        if mtype == 'textcard' or mtype == 'news':
            rd = json_flat_2_tree(reqdata)
        else:
            rd = reqdata.to_dict() if hasattr(reqdata, 'to_dict') else reqdata
        msgr = cls(server, mtype)
        wxuid = rd.get('wxuserid')
        if wxuid:
            msgr.users = wxuid
        else:
            msgr.departid = rd['wxdepartid']
        msgr.content = rd['content']
        return msgr

    def __init__(self, server, mtype='text', *content):
        if mtype not in self.__class__.msgtypes:
            raise ValueError("Message type is not support!")
        self.server = server
        self.msgtype = mtype
        self.departid = ""
        self.content = None
        self.users = ""
        if content:
            if self.msgtype == 'text':
                self.text(content[0])
            elif self.msgtype == 'image':
                self.image(content[0])
            elif self.msgtype == 'textcard':
                self.textcard(*content)
            elif self.msgtype == 'news':
                self.news(content)

    def _gen_pdata(self):
        pdata = {
            'msgtype': self.msgtype,
            'wxuserid': self.users,
            'wxdepartid': self.departid,
            'content': self.content
        }

    def text(self, content, firewall=None):
        if firewall and not firewall(content):
            raise ValueError("Not Allow Message!")
        self.content = content# if isinstance(content, unicode) else content.decode('gbk')
        self.msgtype = 'text'

    def image(self, content, check=False):
        if check:
            if not re.search(r'^http[s]{0,1}\:\/\/.*?\..*', url):
                raise ValueError("Not A URL")
        self.content= content
        self.msgtype = 'image'

    def textcard(self, title, description, url, check=False):
        if check:
            if not re.search(r'^http[s]{0,1}\:\/\/.*?\..*', url):
                raise ValueError("Not A URL")
        self.content = dict(title=title, description=description, url=url)
        self.msgtype = 'textcard'

    def news(self, contents, check=False):
        def chkr(cont):
            if 'title' in cont and 'description' in cont and re.search(r'^http[s]{0,1}\:\/\/.*?\..*', cont['url'])\
             and re.search(r'^http[s]{0,1}\:\/\/.*?\..*', picurl):
                return True
            else:
                return False
        if check:
            for part in contents:
                if not chkr(part):
                    raise ValueError("Error Content!")
        self.content = contents
        self.msgtype = 'news'

    def send2user(self, users):
        wxusers = None
        if isinstance(users, str):
            if ',' in users:
                wxusers = users.replace(',', '|')
            else:
                wxusers = users
        elif isinstance(users, (list, tuple)):
            if isinstance(users[0], str):
                wxusers = '|'.join(users)
            elif isinstance(users[0], dict):
                wxusers = '|'.join([_['wxuserid'] for _ in users])
        if wxusers is None:
            raise ValueError("unknown users!")
        postdata = {
            'msgtype': self.msgtype,
            'wxuserid': wxusers,
            'content': self.content
        }
        purl = self.server.handle_msger(others=dict(data=postdata))
        with app_smsg('post_msg', postdata=postdata, posturl=purl) as pm:
            wx_rtdata = pm.post()
        return wx_rtdata

    def send2dpt(self, departid):
        postdata = {
            'msgtype': self.msgtype,
            'wxdepartid': departid,
            'content': self.content
        }
        purl = self.server.handle_msger(others=dict(data=postdata))
        with app_smsg('post_msg', postdata=postdata, posturl=purl) as pm:
            wx_rtdata = pm.post()
        return wx_rtdata


#   campus version would liter
class app_smsg(object):
    """
    with wx_smsg('get_token', ('grant_type', 'appid', 'secret')) as get_token:
        rt = get_token.get()
    with wx_smsg('get_token', grant_type='', appid='', secret='') as get_token:
    work with server
    """
    baseurl = 'open.campus.qq.com'
    post_header = {"Content-type": "application/json;charset=utf-8"}
    #post_header = {"Content-type": "multipart/form-data;charset=utf-8"}
    get_header = {'Content-Type':'text/html; charset=utf-8'}
    error_rtp = ('errcode', 'errmsg')
    server_schema = 'https'
    SecretKey = ''

    def __init__(self, mapname, params, baseurl='', postdata=None, posturl=''):
        #   post about: postdata/post_struct_name => for self.post_maker
        if mapname.startswith('get'):
            self.mode = 0
            msgset = appmsg_sets
        else:
            self.mode = 1
            msgset = appmsg_sets2
        if mapname not in msgset:
            raise KeyError('msgset mapping name [%s] is not exists!' % mapname)
        self.SecretKey = self.__class__.SecretKey
        self.action,path,self.keys,self.ext = msgset[mapname]
        self.params = params
        # self.ext: get as return pattern; post as post_data_body_name
        self.baseurl = baseurl or self.__class__.baseurl
        self.path = ''
        self.url = ''
        if path:
            if not path.startswith('/'):
                self.path += '/' + path
            else:
                self.path += path
        if self.mode == 1:
            self.postdata = postdata or {}
            self.posturl = posturl

    def _url_maker(self, token, SecretKey=None):
        # token: {}
        SecretKey = SecretKey or self.SecretKey
        sign_source = '%s%s%s?' % ('GET' if self.mode == 0 else 'POST', self.baseurl, self.path)
        cpos = 0
        workd = token.copy()
        workd.update(self.params)
        workd['Timestamp'] = int(time.time())
        workd['Action'] = self.action
        ostr = '&'.join(['%s=%s' % (k, workd[k]) for k in sorted(workd)])
        print(ostr)
        self.url = self.path + '?' + ostr
        print(self.url)
        sign_source += ostr
        if self.mode == 1:
            self.postbody = json.dumps(self.postdata, separators=(',',':'))
            sign_source += '&Date=' + self.postbody
        _signcoded = base64.b16encode(hmac.new(SecretKey.encode(), sign_source.encode(), sha1).digest())
        self.sign = quote(_signcoded.lower(), safe='')
        return self.url

    def _rt_solve(self, rtstring):
        #   check if error!
        rt = json.loads(rtstring)
        if 'code' in rt and int(rt['code']) == 0:
            return rt
        else:
            loger.warning('A Err return From Server!')
            print(rt)
            # on error
            rt['respon'] = 'failure'
            return rt

    def __enter__(self):
        loger.info('enter with con by baseurl: %s' % self.baseurl)
        if self.__class__.server_schema == 'https':
            self.con = httplib.HTTPSConnection(self.baseurl, timeout=5, context=Context)
        else:
            self.con = httplib.HTTPConnection(self.baseurl, timeout=5)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.con.close()

    def _url_file_save(self, url, fp, o=True):
        try:
            uo = urllib.URLopener()
            ufo = uo.open(url)
            data = ufo.read()
            ufo.close()
            fo = open(fp, 'w+b')
            fo.write(data)
            fo.close()
        except:
            loger.warning('not able to save data to file.')
            return None
        return fp

    def get(self, token, filep='', json=True):
        if self.con:
            self._url_maker(token)
            self.con.request('GET', self.url)
            resp = self.con.getresponse()
            if resp.status == 200:
                if filep:
                    fo = None
                    try:
                        fo = open(filep, 'w+b')
                        fo.write(resp.read())
                    except:
                        self._url_file_save(self.url, filep)
                    finally:
                        if fo:
                            fo.close()
                    loger.info('file save done')
                    return filep
                else:
                    #   perhaps get a file some...
                    rt = resp.read()
                    if json:
                        return self._rt_solve(rt)
                    else:
                        return rt
        else:
            raise RuntimeError('NO connection!')

    def post(self, token, json=True, posturl=''):
        self._url_maker(token)
        if self.con:
            self.con.request('POST', self.url, self.postbody.encode(), self.__class__.post_header)
            resp = self.con.getresponse()
            rt = resp.read()
            if json:
                return self._rt_solve(rt)
            return rt
        else:
            raise RuntimeError('NO connection!')

    def expost(self, json=True, posturl='', postdata=None):
        if not postdata:
            self.postdata = dict()
            self.postdata[self.ext] = dict()
        elif self.ext not in postdata:
            tmpd = dict()
            tmpd[self.ext] = self.postdata
            self.postdata = tmpd
        else:
            self.postdata = postdata
        td = self.postdata[self.ext]
        for k in self.keys:
            if k not in td:
                loger.warning('may be ignore key: %s' % k)
        self.posturl = posturl
        return self.post(json=json)


# A cache version of getter
# base on get url without timestamp and sign
# update 20181213, a simple dict could passin an independent cache dict as user's cache
# if we got a redis as cacher:
# redis_cacher(object), __getitem__ <=> redis.get; get() <=> redis.get;
# [MARK: it's good to use double cache for switch mode; it's easy to limit the cache alive and not much resource needed. we can switch cache graceful]
class capp_smsg(app_smsg):
    # smsg with cache
    cacheA_Mark = True
    cacherA = dict()
    cacherB = dict()
    cache = cacherA
    #cache_limit = 128 # just for an easy way
    cache_limit = cur_config.system('cache_size')
    cache_kicks = 32 # when full how many to kick
    cache_kickr = 0.5
    cache_array = [None] * cache_limit
    cpos = 0
    cache_dict_limit = 5 # user seperated dict cache

    clear_time = 600
    switch_time = 3600
    last_time = time.time()
    stimer = None

    C_LOCK = False
    lock_wait = 0.1
    lock_wcount = 10

    @classmethod
    def cclear(cls):
        if cls.cacheA_Mark:
            # when cacher_a works... clear cacherB
            _ = cls.cacherB
        else:
            _ = cls.cacherA
        loger.info("600s and clear on cache: %s with len: %d" % ('cacherB' if cls.cacheA_Mark else 'cacherA', len(_)))
        _.clear()

    @classmethod
    def via_cache(cls, cpath, force=False):
        # check for switch cache
        dt = time.time() - cls.last_time
        if dt > cls.switch_time:
            loger.info("time to switch cacher")
            cls.cacheA_Mark = not cls.cacheA_Mark
            cls.cache = cls.cacherA if cls.cacheA_Mark else cls.cacherB
            cls.cache_array = []
            cls.last_time = time.time()
            cls.stimer = Timer(cls.clear_time, cls.cclear)
            cls.stimer.start()
        # real job
        cpath = cls._xpath(cpath)
        if cpath in cls.cache:
            loger.info("%s in cache!" % cpath)
            if cls.C_LOCK is True:
                loger.debug("CHECK: wait for C_LOCK!")
                t = cls.lock_wcount
                while t > 0:
                    time.sleep(cls.lock_wait)
                    if cls.C_LOCK is False:
                        break
                    t -= 1
                loger.error("CHECK: wait for C_CLOCK over time!")
                return None
            cls.cache_array.remove(cpath)
            cls.cache_array.insert(0, cpath)
            return cls.cache[cpath]
        else:
            return None

    @classmethod
    def store_cache(cls, cpath, data):
        cpath = cls._xpath(cpath)
        loger.info("%s store into cache!" % cpath)
        if cls.cpos >= cls.cache_limit and cls.C_LOCK is False:
            loger.info("cache ups to limit! do short clear.")
            _times = min(cls.cache_kicks, round(len(cls.cache) * cls.cache_kickr))
            cls.C_LOCK = True
            for x in xrange(_times):
                xpath = cls.cache_array.pop()
                try:
                    cls.cache.pop(xpath)
                except KeyError:
                    loger.warn("pop cache error with key: %s" % xpath)
                    continue
            cls.cpos = len(cls.cache) - 1
            cls.C_LOCK = False
        cls.cpos += 1
        loger.debug("cache size: %s!" % cls.cpos)
        cls.cache_array.append(cpath)
        cls.cache[cpath] = data
        return True

    @classmethod
    def clear_cache(cls, xurl):
        try:
            cls.cache.pop(xurl)
        except KeyError:
            return False
        cls.cpos -= 1
        return True

    @classmethod
    def _xpath(cls, opath):
        pstr = re.sub(r'&timestamp=.*?&', '&', opath)
        return pstr[:pstr.index('&sign')]

    # inject a cacher
    def __init__(self, mapname, params, baseurl='', postdata=None, posturl=''):
        # any thing?
        super(capp_smsg, self).__init__(mapname, params, baseurl='', postdata=None, posturl='')

    def get(self, filep='', urlpath='', json=True, force=False, viacache=True):
        self._url_maker(token)
        if not force:
            rt = self.__class__.via_cache(self.url)
            if rt is not None:
                if json:
                    return self._rt_solve(rt)
                else:
                    return rt
        if self.con:
            #turl = purl.encode('utf8') if isinstance(purl, unicode) else purl.decode('gbk').encode('utf8')
            #turl = purl.encode('utf8')
            self.con.request('GET', self.url)
            resp = self.con.getresponse()
            if resp.status == 200:
                if filep:
                    fo = None
                    try:
                        fo = open(filep, 'w+b')
                        fo.write(resp.read())
                    except:
                        self._url_file_save(purl, filep)
                    finally:
                        if fo:
                            fo.close()
                    loger.info('file save done')
                    return filep
                else:
                    #   perhaps get a file some...
                    rt = resp.read()
                    if not json:
                        return rt
                    rt_json = self._rt_solve(rt)
                    if viacache and rt_json['code'] == 0 and 'data' in rt_json:
                        self.__class__.store_cache(purl, rt)
                    return rt_json
        else:
            raise RuntimeError('NO connection!')