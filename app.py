from flask import Flask, request, jsonify
from flask_cors import CORS
import pymysql
import datetime
import requests
import urllib.parse
import json
from threading import Thread

# 将当前文件夹(.)设为静态网页目录
app = Flask(__name__, static_folder='.', static_url_path='')
# 允许跨域请求，方便前端直接在本地浏览器打开 html 测试
CORS(app)

# 新增路由：当访问根目录时，默认返回登录页
@app.route('/')
def index():
    return app.send_static_file('login.html')

# ==========================================
# 钉钉机器人配置
# ==========================================
DINGTALK_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=fe3443254d8525998f8fc70a37721af553cf8f46b50f468efb5f23ae7a4f7f19"

def send_dingtalk_msg(content):
    """异步发送钉钉机器人消息，避免阻塞主线程"""
    def send_task(msg_content):
        headers = {'Content-Type': 'application/json'}
        data = {
            "msgtype": "text",
            "text": {
                # 消息正文中必须包含钉钉设置的安全验证关键词（这里默认包含“线索”）
                "content": f"【线索】提醒：\n{msg_content}" 
            }
        }
        try:
            response = requests.post(DINGTALK_WEBHOOK, data=json.dumps(data), headers=headers, timeout=5)
            print(f"钉钉推送返回: {response.text}")
        except Exception as e:
            print(f"钉钉推送异常: {e}")

    # 使用线程异步发送，保证前端响应速度
    Thread(target=send_task, args=(content,)).start()

# ==========================================
# 数据库配置 (请根据实际情况修改)
# ==========================================
DB_CONFIG = {
    'host': '127.0.0.1',
    'port': 3306,
    'user': 'root',
    'password': 'SMT2010_hm',          # 注意：这里千万不要填空格，就是两个单引号！
    'database': 'clue_db',   # 刚才创建的数据库名字
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

def get_db_connection():
    return pymysql.connect(**DB_CONFIG)

# ==========================================
# 1. 登录接口
# ==========================================
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    account = data.get('account')
    password = data.get('password')

    if not account or not password:
        return jsonify({'code': 400, 'message': '请输入账号和密码'})

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # MVP版本暂用明文比对，生产环境强烈建议使用 bcrypt 等加密校验
            sql = "SELECT id, user_name, status FROM sys_user WHERE account=%s AND password=%s"
            cursor.execute(sql, (account, password))
            user = cursor.fetchone()

            if user:
                if user['status'] == 0:
                    return jsonify({'code': 403, 'message': '该账号已被禁用'})
                
                # 登录成功，返回用户信息。
                return jsonify({
                    'code': 200, 
                    'message': '登录成功', 
                    'data': {
                        'token': str(user['id']), # 用 user_id 模拟 token
                        'user_name': user['user_name']
                    }
                })
            else:
                return jsonify({'code': 401, 'message': '账号或密码错误'})
    finally:
        conn.close()

# ==========================================
# 2. 提报记录 (添加线索 / 获取线索列表)
# ==========================================
@app.route('/api/clues', methods=['GET', 'POST'])
def clues():
    user_id = request.headers.get('Authorization', '1') # 默认用 1 作为测试

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            if request.method == 'POST':
                # 添加线索
                data = request.json
                phone = data.get('phone')
                
                if not phone or len(phone) != 11:
                    return jsonify({'code': 400, 'message': '手机号格式不正确'})

                # 插入数据，默认 status 为 0 (表示无需求)，type 为 0 (自主提报), is_checkout 默认为 0
                sql = """
                    INSERT INTO biz_clue (user_id, phone, status, type, is_checkout, create_time) 
                    VALUES (%s, %s, 0, 0, 0, %s)
                """
                cursor.execute(sql, (user_id, phone, datetime.datetime.now()))
                conn.commit()

                # ====== 新增：钉钉推送提醒 ======
                msg = f"收到新线索！来源：自主提报，手机号：{phone}"
                send_dingtalk_msg(msg)
                # ==============================

                return jsonify({'code': 200, 'message': '添加成功'})

            elif request.method == 'GET':
                # 获取提报记录列表: 此时只查 status = 0 的数据
                sql = """
                    SELECT phone, type, status, create_time 
                    FROM biz_clue 
                    WHERE user_id=%s AND status = 0 
                    ORDER BY create_time DESC
                """
                cursor.execute(sql, (user_id,))
                rows = cursor.fetchall()
                
                # 类型映射字典（兼顾老数据的 '自主提交' 和新设定的 0, 1）
                type_map = {0: '自主提报', '0': '自主提报', 1: '报器价', '1': '报器价', '自主提交': '自主提报'}

                # 格式化数据以匹配前端展示
                formatted_data = []
                for row in rows:
                    # 翻译数据库存储的值到前端显示文本
                    mapped_type = type_map.get(row['type'], str(row['type']))
                    
                    formatted_data.append({
                        'phone': row['phone'],
                        'type': mapped_type,
                        'status': '无需求', # 强制返回无需求
                        'submitTime': row['create_time'].strftime('%Y-%m-%d %H:%M:%S') if row['create_time'] else ''
                    })
                    
                return jsonify({'code': 200, 'data': formatted_data})
    finally:
        conn.close()

# ==========================================
# 2.5 报价器专属提报接口
# ==========================================
@app.route('/api/quotation/submit', methods=['POST'])
def quotation_submit():
    data = request.json
    phone = data.get('phone')
    user_id = data.get('user_id')  # 从分享链接中带过来的分享人ID

    if not phone or len(phone) != 11:
        return jsonify({'code': 400, 'message': '手机号格式不正确'})
    if not user_id:
        return jsonify({'code': 400, 'message': '缺少分享人信息'})

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # 插入数据，type 为 1 (报器价), is_checkout为0
            sql = """
                INSERT INTO biz_clue 
                (user_id, phone, status, type, is_checkout, create_time) 
                VALUES (%s, %s, 0, 1, 0, %s)
            """
            cursor.execute(sql, (user_id, phone, datetime.datetime.now()))
            conn.commit()

            # ====== 新增：钉钉推送提醒 ======
            msg = f"收到新线索！来源：报价器分享，手机号：{phone}，分享人ID：{user_id}"
            send_dingtalk_msg(msg)
            # ==============================

            return jsonify({'code': 200, 'message': '提交成功'})
    except Exception as e:
        return jsonify({'code': 500, 'message': '服务器异常: ' + str(e)})
    finally:
        conn.close()

# ==========================================
# 3. 需求列表 (获取需求列表及统计)
# ==========================================
@app.route('/api/needs', methods=['GET'])
def needs():
    user_id = request.headers.get('Authorization', '1')

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # 1. 查询需求列表 (现在是 status = 1)
            sql_list = """
                SELECT phone, city, address, commission, need_submit_time, is_checkout 
                FROM biz_clue 
                WHERE user_id=%s AND status=1 
                ORDER BY need_submit_time DESC
            """
            cursor.execute(sql_list, (user_id,))
            list_rows = cursor.fetchall()

            # 2. 统计提交问题 (is_checkout=0 的总数，且 status=1)
            sql_count = "SELECT COUNT(*) as total FROM biz_clue WHERE user_id=%s AND status=1 AND is_checkout=0"
            cursor.execute(sql_count, (user_id,))
            total_uncheckout = cursor.fetchone()['total'] or 0

            # 3. 统计待结算佣金 (is_checkout=0 的总佣金之和，且 status=1)
            sql_commission = "SELECT SUM(commission) as total_comm FROM biz_clue WHERE user_id=%s AND status=1 AND is_checkout=0"
            cursor.execute(sql_commission, (user_id,))
            total_commission = cursor.fetchone()['total_comm'] or 0.00

            # 格式化列表数据
            formatted_list = []
            for row in list_rows:
                formatted_list.append({
                    'phone': row['phone'],
                    'city': row['city'] or '未分配',
                    'address': row['address'] or '未分配',
                    'commission': float(row['commission']),
                    'is_checkout': row['is_checkout'], # 下发结算状态给前端
                    'submitTime': row['need_submit_time'].strftime('%Y-%m-%d %H:%M:%S') if row['need_submit_time'] else ''
                })

            # 组装返回给前端的最终结构
            return jsonify({
                'code': 200,
                'data': {
                    'summary': {
                        'total': total_uncheckout,
                        'totalCommission': float(total_commission)
                    },
                    'list': formatted_list
                }
            })
    finally:
        conn.close()

if __name__ == '__main__':
    # 启动在 8888 端口，前端配的就是这个
    app.run(host='0.0.0.0', port=8888, debug=True)