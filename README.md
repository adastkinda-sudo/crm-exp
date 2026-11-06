# 金融产品推荐平台

金融产品推荐平台面向客户提供基金和银行理财产品的浏览、检索与详情查看。当前目录包含 30 个基金和 20 个银行理财产品。平台支持注册、登录、客户账号切换和管理员商品目录查看，产品中心支持关键词、类别和 R1–R5 风险等级筛选。

## 本地运行

在项目根目录执行：

```bash
python3 backend/server.py
```

然后打开 <http://127.0.0.1:8000>。

## 功能

- 首页个性化精选与产品浏览
- 按需求浏览产品类别
- 产品关键词搜索
- 产品类别和风险等级筛选
- 注册、登录与账号切换
- 产品详情查看与收藏状态
- 产品封面、收益说明、资料日期和原始来源链接
- 推荐服务接口和本地数据服务

## 目录

```text
backend/server.py       API 与静态文件服务
backend/database.py     SQLite 数据库初始化和查询
backend/catalog.py      商品校验与事务导入
data/seed.json          初始账号与客户资料
data/actual_products.json  当前商品来源数据
data/actual_products_sources.md  来源与口径说明
data/financial_products.sqlite3 运行时数据库
scripts/import_actual_products.py  商品目录导入工具
frontend/index.html     客户端页面结构
frontend/styles.css     视觉样式和响应式布局
frontend/app.js         页面交互和接口调用
frontend/assets/        本地产品图片
docs/                   项目设计文档
```

首次启动时，服务根据 `data/actual_products.json` 初始化商品目录，根据 `data/seed.json` 创建初始账号与客户资料。后续请求直接读取 SQLite。启动时只在来源数据内容变化时迁移商品，相同数据重复启动不会覆盖数据库中的修改。

本地种子账号包括 `lin`、`chen`、`wang`、`zhao`、`zhou` 和 `admin`。首次启动时会生成本地凭据文件 `data/.credentials.json`，该文件被 Git 忽略，不会进入远程仓库。

## 导入商品目录

数据目录需包含 `products.json`、对应的 `images/` 图片，可附带 `DATA_SOURCES.md`：

```bash
python3 scripts/import_actual_products.py /path/to/financial_recommender
```

导入前验证字段、编号、数值与图片。商品变更在事务中执行，已有数据库自动备份到被 Git 忽略的 `data/backups/`。账号和客户资料保留；当商品编号对应的产品发生变化时，旧行为记录移至 `archived_behaviors`，不用于新目录的推荐。首次切换商品来源时，历史行为全部归档，不生成虚构行为。相同内容重复导入不会重复归档。

验证：`python3 -m unittest discover -s tests -v`。

## 数据口径

当前数据由提供的目录导入，资料日期为 2026-11-06，来源明细见 [数据来源与口径说明](data/actual_products_sources.md)。平台未重新核验发行机构的实时信息。

原始图片保存在 `frontend/assets/products/`。部分图内数字与结构化数据不一致，因此页面使用机构与产品标识封面，商品数值统一读取结构化目录。

基金展示近一年历史收益；银行理财展示业绩比较基准原文。13 条缺少可比数值的记录保持为空，不补造收益，也不将不同收益口径混排。部分起购金额在来源中为估值，客户端提示待确认，此类产品不进入按预算推荐。基金风险等级在来源中按产品类型映射，部分申赎安排也经过简化，均须以销售机构正式资料为准。产品状态按采集资料呈现，不代表当前可购买。

历史收益不代表未来表现，业绩比较基准不代表收益承诺。具体收益、费用、风险等级、申赎安排和服务条款应以发行或销售机构的正式文件为准。
