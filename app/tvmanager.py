import os
import re
from app.utils.commons import singleton
from app.media.meta._base import MetaBase
from config import Config,RMT_MEDIAEXT
from app.media import Media
from app.media.scraper import Scraper
from app.utils.types import MediaType,MediaServerType
from app.helper import DbHelper
from app.media.meta.metavideov2 import MetaVideoV2
import log
import json
from app.db.models import LOCALSTATUSTV
import copy

@singleton
class TVManager:
    dbhelper = None
    
    def __init__(self):
        self.init_config()

    def init_config(self):
        self.dbhelper = DbHelper()        
        media = Config().get_config('media')
        if media:
            # 电视剧目录
            self._tv_path = media.get('tv_path')
            if not isinstance(self._tv_path, list):
                if self._tv_path:
                    self._tv_path = [self._tv_path]
                else:
                    self._tv_path = []
    
    #########
    ## 订阅
    #########





    ###########
    # 定时任务
    ###########

    def update_pdb_tv_info(self, tvseries):
        """
        替代app.helper.metahelper?
        """
        pass


    def get_local_tv_info_by_mediaserver(self, mediaserver:MediaServerType):
        '''
        获取本地电视剧信息（通过读取媒体库）

        '''
        pass

    def get_local_tv_info(self, tvpath):
        """
        获取本地电视剧信息（通过读取文件系统）

        :param tvpath: 电视剧名称
        :return: 电视剧信息
        """
        # 简易获取电视剧名
        foldername = os.path.basename(tvpath)
        tv_title = re.sub(r'\[..dbid\=.+\]', '', foldername).strip()
        tv_title = re.sub(r'\((?:19[0-9]|20[012])[0-9]\)', '', tv_title).strip()

        tv_meta = MetaBase(title=tv_title)

        tv_meta.title = tv_title
        tv_meta.filePath = tvpath
        tv_meta.media_type = MediaType.TV

        # 媒体ID
        if foldername.find('[tmdbid=') != -1:
            tv_meta.tmdb_id = int(foldername[foldername.find('[tmdbid=') + 8:foldername.find(']')])
        elif foldername.find('[imdbid=') != -1:
            tv_meta.imdb_id = foldername[foldername.find('[imdbid=') + 8:foldername.find(']')]
        elif foldername.find('[tvdbid=') != -1:
            tv_meta.tvdb_id = int(foldername[foldername.find('[tvdbid=') + 8:foldername.find(']')])
        else:
            # 文件路径找不到id，就读取文件夹下nfo文件
            tv_nfo = os.path.join(tvpath, 'tvshow.nfo')
            if os.path.exists(tv_nfo):
                tv_meta.tmdb_id = Scraper().get_tmdbid_from_nfo(tv_nfo)
            else:
                return None
        

        # 当前路径下存在的季和集，保存在note中
        local_se = {}

        # 遍历tvpath下的所有文件，找到所有的SxxExx的媒体文件，匹配出季和集
        for root, dirs, files in os.walk(tvpath):
            for file in files:
                if os.path.splitext(file)[-1].lower() in RMT_MEDIAEXT:
                    meta_info = MetaVideoV2(title=file, subtitle='', fileflag=True, filePath=os.path.join(root, file),media_type=MediaType.TV)
                    season_num = meta_info.begin_season
                    episode_num = meta_info.begin_episode
                    if season_num not in local_se.keys():
                        local_se[season_num] = []
                    local_se[season_num].append(episode_num)
        
        tv_meta.note['local_se'] = local_se
        
        return tv_meta

    def get_tv_path_conf(self):
        return self._tv_path

    def get_local_tv_meta_list(self,paths=None):
        """
        获取本地电视剧
        :return: 电视剧列表
        """
        tvs = []
        # 如果没有指定路径，就扫描所有媒体库
        if paths:
            scan_paths = paths
        else:
            scan_paths = self._tv_path
        for path in scan_paths:
            log.info('【TVManager】正在刷新电视剧目录状态：{}'.format(path))
            if os.path.exists(path):
                subdirs = os.listdir(path)
                fullpaths = [os.path.join(path, subdir) for subdir in subdirs]
                tvs.extend(fullpaths)
        local_tv_meta_list = []
        ##测试仅获取前20个，正式注意删除！
        #tvs = tvs[:20]
        for tv in tvs:
            local_tv_meta = self.get_local_tv_info(tv)
            if local_tv_meta:
                local_tv_meta_list.append(local_tv_meta)
        return local_tv_meta_list
    
    def find_missing_season(self, local_tv_meta:MetaBase):
        """
        比较本地电视剧和tmdb数据，找到缺失的季
        :param local_tv_meta: 本地电视剧
        :param tmdb_info: tmdb数据
        :return: None
        """
        
        local_se = local_tv_meta.note.get('local_se')
        tmdb_se = {}
        if local_tv_meta.tmdb_info:
            tmdb_info = local_tv_meta.tmdb_info
        if tmdb_info.get('seasons'):
            for season in tmdb_info['seasons']:
                tmdb_se[season['season_number']] = list(range(1, season['episode_count'] + 1))
        
        missing_se = {}
        # 比对
        for season in tmdb_se.keys():
            if season not in local_se.keys():
                missing_se[season] = 'all'
            else:
                missing_episode = list(set(tmdb_se[season]) - set(local_se[season]))
                missing_se[season] = missing_episode
        
        local_tv_meta.note['missing_se'] = missing_se
        local_tv_meta.note['tmdb_se'] = tmdb_se
        return local_tv_meta

    def merge_same_tv(self, tv_meta_list):

        # 相同tmdbid的电视剧成组
        tv_meta_group = {}
        for tv_meta in tv_meta_list:
            if tv_meta.tmdb_id:
                if tv_meta.tmdb_id not in tv_meta_group.keys():
                    tv_meta_group[tv_meta.tmdb_id] = []
                tv_meta_group[tv_meta.tmdb_id].append(tv_meta)

        commit_list = []

        # 组内合并
        for tmdb_id in tv_meta_group.keys():
            season_missing_info = {}
            paths = []
            tv_metas = tv_meta_group[tmdb_id]
            tmdb_info = tv_metas[0].tmdb_info
            full_season = True
            for tv_meta in tv_metas:
                missing_se = tv_meta.note.get('missing_se')
                for season,missing_episode in missing_se.items():
                    # TODO: 如果已经SUBSCRIBE了，就SUBSCRIBE:1
                    # season变成两位str
                    season = str(season).zfill(2)
                    if season not in season_missing_info.keys():
                        season_missing_info[season] = {'DUP': 0,'MISSING_INFO':''}
                    if not missing_episode:
                        season_missing_info[season]['DUP'] += 1
                        continue
                    else:
                        full_season = False
                    # 判断是不是一整季没了
                    if missing_episode != 'all':
                        season_missing_info[season]['DUP'] += 1
                    else:
                        continue

                    # MISSING_INFO格式化
                    # 只有一个的就不写路径了
                    if len(tv_metas) > 1:
                        missing_info = tv_meta.filePath + ':'
                    else:
                        missing_info = ''
                    for episode in missing_episode:
                        missing_info += 'E{},'.format(episode)
                    missing_info.strip(',')
                    if season_missing_info[season]['MISSING_INFO']:
                        season_missing_info[season]['MISSING_INFO'] += '<br>'
                    season_missing_info[season]['MISSING_INFO'] += missing_info
                paths.append(tv_meta.filePath)
                if not tv_meta.filePath:
                    pass
            
            # 预备提交体
            insert_tv_missing_info = LOCALSTATUSTV(
                CATEGORY=tv_metas[0].category,
                TMDBID=tmdb_id,
                TITLE=tmdb_info['name'],
                YEAR=tmdb_info['first_air_date'][:4],
                REL_TV_ID=0,
                FULL_SEASON=int(full_season),
                SEASON_MISSING_INFO=json.dumps(season_missing_info),
                IGNORE_MISSING=0,
                PATHS='|'.join(paths))
    

            commit_list.append(insert_tv_missing_info)

        return commit_list

    
    def commit_missing_season(self, commit_list):
        """
        提交缺失的季
        :param tv_meta: 电视剧
        :return: None
        """
        self.dbhelper.clear_local_status_tv()
        self.dbhelper.batch_insert_local_status_tv(commit_list)
        

    ##########
    # 前端交互
    ##########
    def refresh_local_status_tv(self,paths=None):
        """
        比较本地电视剧和数据库电视剧
        :param tvseries: 电视剧列表
        :return: 电视剧列表
        """
        local_tv_meta_list= self.get_local_tv_meta_list(paths=paths)
        tv_meta_mlist = []


        # 获取带missing数据的meta
        for local_tv_meta in local_tv_meta_list:
            if local_tv_meta.tmdb_id:
                tmdb_info = Media().get_tmdb_info(tmdbid=local_tv_meta.tmdb_id,mtype=MediaType.TV,append_to_response="all")
                if tmdb_info:
                    local_tv_meta.set_tmdb_info(tmdb_info)
                    local_tv_meta = self.find_missing_season(local_tv_meta)
                    tv_meta_mlist.append(local_tv_meta)
                else:
                    log.info('电视剧{}的tmdbid{}在tmdb上找不到'.format(local_tv_meta.filePath, local_tv_meta.tmdb_id))
            else:
                log.info('电视剧{}没有tmdbid'.format(local_tv_meta.filePath))
        
        # 合并相同tmdbid的电视剧
        commit_list = self.merge_same_tv(tv_meta_mlist)
        # 提交
        self.commit_missing_season(commit_list)
        
    def refresh_local_status_tv_by_ids(self,tvids):
        """
        刷新指定的电视剧状态
        param tvids:电视剧id列表
        return:None
        """
        for tvid in tvids:
            # 查询db里指定id的电视剧，获取PATHS
            db_tv_info = self.dbhelper.get_local_status_tv_by_id(tvid)
            log.info('【TVManager】正在更新电视剧状态：{}'.format(db_tv_info.TITLE))
            db_tv_paths = db_tv_info.PATHS.split('|')
            db_tv_tmdbid = db_tv_info.TMDBID
            db_ignore_missing = db_tv_info.IGNORE_MISSING
            # 检查所有路径里是否有新的tmdbid相同的电视剧，如果有，就加进PATHS里
            for path in self._tv_path:
                if os.path.exists(path):
                    subdirs = os.listdir(path)
                    fullpaths = [os.path.join(path, subdir) for subdir in subdirs]
                    for fullpath in fullpaths:
                        if fullpath in db_tv_paths:
                            continue
                        else:
                            tmdbid_str = '[tmdbid={}]'.format(db_tv_tmdbid)
                            if tmdbid_str in fullpath:
                                db_tv_paths.append(fullpath)
                                continue
                            if '[tmdbid=' in fullpath:
                                continue
                            tv_nfo = os.path.join(fullpath, 'tvshow.nfo')
                            if os.path.exists(tv_nfo):
                                tv_meta = self.get_local_tv_info(fullpath)
                                if tv_meta.tmdb_id == db_tv_tmdbid:
                                    db_tv_paths.append(fullpath)
                                    continue
            # 遍历PATHS，获取所有电视剧的信息
            tv_meta_list = []
            
            tmdb_info = Media().get_tmdb_info(tmdbid=db_tv_tmdbid,mtype=MediaType.TV,append_to_response="all")
            if not tmdb_info:
                log.error('【TVManager】电视剧{}的tmdbid{}找不到'.format(db_tv_info.TITLE, db_tv_info.TMDBID))
                continue
            for path in db_tv_paths:
                tv_meta = self.get_local_tv_info(path)
                tv_meta.set_tmdb_info(tmdb_info)
                tv_meta = self.find_missing_season(tv_meta)
                tv_meta_list.append(tv_meta)
            # merge_same_tv
            commit_list = self.merge_same_tv(tv_meta_list)
            if commit_list:
                commit = commit_list[0]
                update_dict = {}
                update_dict['FULL_SEASON'] = commit.FULL_SEASON
                update_dict['SEASON_MISSING_INFO'] = commit.SEASON_MISSING_INFO
                update_dict['PATHS'] = commit.PATHS
                # 按照tvid update新的信息
                self.dbhelper.update_local_status_tv(tvid,update_dict)
            else:
                log.error('【TVManager】电视剧{}更新错误'.format(db_tv_info.TITLE))



    def get_local_status_tv(self,search, page, rownum,full_season):
        """
        获取电视剧的状态
        :param tvseries: 电视剧名称
        :return: 所有季
        """
        return self.dbhelper.get_local_status_tv(search, page, rownum,full_season)
    
    def subscribe_missing_season(self, tvids):
        """
        订阅缺失的季
        :param tvids: 电视剧id列表
        :return: None
        """
        for tvid in tvids:
            db_tv_info = self.dbhelper.get_local_status_tv_by_id(tvid)
            db_tv_title = db_tv_info.TITLE
            log.info('【TVManager】已订阅电视剧缺失的季：{}'.format(db_tv_title))

    def ignore_missing_season(self, tvid):
        """
        忽略缺失的季
        :param tvid: 电视剧id
        :return: None
        """
        db_tv_info = self.dbhelper.get_local_status_tv_by_id(tvid)
        db_tv_title = db_tv_info.TITLE
        log.info('【TVManager】已忽略电视剧缺失的季：{}'.format(db_tv_title))
        self.dbhelper.update_local_status_tv(tvid,{"IGNORE_MISSING":1})


    def delete_tv(self, tvids):
        """
        删除电视剧
        :param tvids: 电视剧id列表
        :return: None
        """
        for tvid in tvids:
            db_tv_info = self.dbhelper.get_local_status_tv_by_id(tvid)
            db_tv_title = db_tv_info.TITLE
            log.info('【TVManager】已删除电视剧：{}'.format(db_tv_title))


