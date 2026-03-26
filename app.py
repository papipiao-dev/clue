from flask import Flask, request, jsonify
from flask_cors import CORS
import pymysql
import datetime

# 将当前文件夹(.)设为静态网页目录
app = Flask(__name__, static_folder='.', static_url_path='')
# 允许跨域请求，方便前端直接在本地浏览器打开 html 测试
CORS(app)

# 新增路由：当访问根目录时，默认返回登录页
@app.route('/')
def index():
    return app.send_static_file('login.html')

# ==========================================
# 数据库配置 (请根据实际情况修改)
# ==========================================
DB_CONFIG = {
    'host': '127.0.0.1',
    'port': 3306,
    'user': 'root',
    'password': '',          # 注意：这里千万不要填空格，就是两个单引号！
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
                # 实际项目中这里应该生成 JWT Token 返回，为了演示精简，我们直接返回 user_id 
                # 前端后续请求可以在 Header 中带上这个 user_id 代表身份
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
    # 模拟从 Header 提取 token (即 user_id)
    # 实际开发中前端需要在 fetch 的 headers 里加上: 'Authorization': '1'
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

                # 插入数据，默认 status 为 0 (线索-待回访)
                sql = """
                    INSERT INTO biz_clue (user_id, phone, status, type, create_time) 
                    VALUES (%s, %s, 0, '自主提交', %s)
                """
                cursor.execute(sql, (user_id, phone, datetime.datetime.now()))
                conn.commit()
                return jsonify({'code': 200, 'message': '添加成功'})

            elif request.method == 'GET':
                # 获取提报记录列表: status IN (0, 2) 分别代表待回访、无需求
                sql = """
                    SELECT phone, type, status, create_time 
                    FROM biz_clue 
                    WHERE user_id=%s AND status IN (0, 2) 
                    ORDER BY create_time DESC
                """
                cursor.execute(sql, (user_id,))
                rows = cursor.fetchall()
                
                # 格式化数据以匹配前端展示
                formatted_data = []
                for row in rows:
                    status_text = '待回访' if row['status'] == 0 else '无需求'
                    formatted_data.append({
                        'phone': row['phone'],
                        'type': row['type'],
                        'status': status_text,
                        'submitTime': row['create_time'].strftime('%Y-%m-%d %H:%M:%S') if row['create_time'] else ''
                    })
                    
                return jsonify({'code': 200, 'data': formatted_data})
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
            # 1. 查询需求列表 (status = 3)
            sql_list = """
                SELECT phone, city, address, commission, need_submit_time 
                FROM biz_clue 
                WHERE user_id=%s AND status=3 
                ORDER BY need_submit_time DESC
            """
            cursor.execute(sql_list, (user_id,))
            list_rows = cursor.fetchall()

            # 2. 统计提交问题 (is_checkout=0 的总数)
            sql_count = "SELECT COUNT(*) as total FROM biz_clue WHERE user_id=%s AND status=3 AND is_checkout=0"
            cursor.execute(sql_count, (user_id,))
            total_uncheckout = cursor.fetchone()['total'] or 0

            # 3. 统计合计佣金 (is_checkout=1 的总佣金)
            sql_commission = "SELECT SUM(commission) as total_comm FROM biz_clue WHERE user_id=%s AND status=3 AND is_checkout=1"
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