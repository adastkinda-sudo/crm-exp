# 金融产品推荐平台

金融产品推荐平台面向客户提供基金、保险、银行理财、信用卡和贷款产品的浏览、检索与详情查看。平台支持账号登录、不同客户账号切换和管理员商品目录管理。首页提供精选产品和热门产品，产品中心支持关键词、类别和风险等级筛选。

## 本地运行

在项目根目录执行：

```bash
python3 backend/server.py
```

然后打开 <http://127.0.0.1:8000>。

## 功能

- 首页精选与热门产品
- 按需求浏览产品类别
- 产品关键词搜索
- 产品类别和风险等级筛选
- 注册、登录与账号切换
- 产品详情查看与收藏状态
- 本地产品图片和产品资料
- 推荐服务接口和本地数据服务

## 目录

```text
backend/server.py       API 与静态文件服务
backend/database.py     SQLite 数据库初始化和查询
data/seed.json          SQLite 初始化数据
data/financial_products.sqlite3 运行时数据库
frontend/index.html     客户端页面结构
frontend/styles.css     视觉样式和响应式布局
frontend/app.js         页面交互和接口调用
frontend/assets/        本地产品图片
docs/                   项目设计文档
```

首次启动时，服务会根据 `data/seed.json` 自动初始化 `data/financial_products.sqlite3`。后续请求直接读取 SQLite 数据库。

本地种子账号包括 `lin`、`chen`、`wang`、`zhao`、`zhou` 和 `admin`。首次启动时会生成本地凭据文件 `data/.credentials.json`，该文件被 Git 忽略，不会进入远程仓库。

产品信息来自本地数据文件，具体收益、费用、风险等级和服务条款应以发行机构的正式文件为准。
