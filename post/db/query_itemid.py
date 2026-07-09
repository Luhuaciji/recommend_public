      
#!/usr/bin/env python3
import sys
import sqlite3
import json
import logging
from datetime import datetime
import argparse

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

from pathlib import Path
DB_FILE = Path(__file__).parent.parent / "product_data.db"

def query_by_itemid(itemid, output_jsonl=False, jsonl_filename=None):
    """按 itemid 查询商品数据"""
    logger.info(f"🔍 开始查询 itemid = {itemid}")
    
    try:
        conn = sqlite3.connect(DB_FILE)
        logger.info(f"✅ 成功连接数据库 {DB_FILE}")
        
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM item_details WHERE itemid = ?", (itemid,))
        logger.info(f"📝 执行 SQL: SELECT * FROM item_details WHERE itemid = '{itemid}'")
        
        row = cursor.fetchone()
        
        if not row:
            logger.warning(f"⚠️ 未找到 itemid = {itemid} 的数据")
            print(f"❌ 未找到 itemid = {itemid} 的数据")
            return None
        
        logger.info(f"✅ 找到 itemid = {itemid} 的数据")
        
        columns = [desc[0] for desc in cursor.description]
        result = {}
        for i, col in enumerate(columns):
            result[col] = row[i]
        
        # 输出到控制台
        print(f"✅ 找到 itemid = {itemid} 的数据：\n")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        
        # 如果需要输出为 jsonl
        if output_jsonl:
            output_file = jsonl_filename or f"query_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
            logger.info(f"📤 开始输出结果到 JSONL 文件: {output_file}")
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(json.dumps(result, ensure_ascii=False) + '\n')
            logger.info(f"✅ 成功输出结果到 {output_file}")
            print(f"\n📤 结果已保存到: {output_file}")
        
        return result
        
    except sqlite3.OperationalError as e:
        logger.error(f"❌ 数据库错误：{e}")
        print(f"❌ 数据库错误：{e}")
        return None
    except Exception as e:
        logger.error(f"❌ 未知错误：{e}", exc_info=True)
        print(f"❌ 未知错误：{e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()
            logger.info("🔌 数据库连接已关闭")

def batch_query_by_itemid(itemids, output_jsonl=False, jsonl_filename=None):
    """批量查询多个 itemid"""
    logger.info(f"🔍 开始批量查询 {len(itemids)} 个 itemid")
    logger.info(f"📋 要查询的 itemid: {', '.join(itemids)}")
    
    try:
        conn = sqlite3.connect(DB_FILE)
        logger.info(f"✅ 成功连接数据库 {DB_FILE}")
        
        cursor = conn.cursor()
        
        placeholders = ','.join(['?'] * len(itemids))
        sql = f"SELECT * FROM item_details WHERE itemid IN ({placeholders})"
        logger.info(f"📝 执行 SQL: {sql}")
        
        cursor.execute(sql, itemids)
        rows = cursor.fetchall()
        
        if not rows:
            logger.warning("⚠️ 未找到任何匹配的数据")
            print("❌ 未找到任何匹配的数据")
            return []
        
        logger.info(f"✅ 找到 {len(rows)} 条数据")
        
        columns = [desc[0] for desc in cursor.description]
        results = []
        for row in rows:
            result = {}
            for i, col in enumerate(columns):
                result[col] = row[i]
            results.append(result)
        
        # 输出到控制台
        print(f"✅ 找到 {len(results)} 条数据：\n")
        print(json.dumps(results, ensure_ascii=False, indent=2))
        
        # 如果需要输出为 jsonl
        if output_jsonl:
            output_file = jsonl_filename or f"query_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
            logger.info(f"📤 开始输出结果到 JSONL 文件: {output_file}")
            with open(output_file, 'w', encoding='utf-8') as f:
                for result in results:
                    f.write(json.dumps(result, ensure_ascii=False) + '\n')
            logger.info(f"✅ 成功输出结果到 {output_file}")
            print(f"\n📤 结果已保存到: {output_file}")
        
        return results
        
    except sqlite3.OperationalError as e:
        logger.error(f"❌ 数据库错误：{e}")
        print(f"❌ 数据库错误：{e}")
        return []
    except Exception as e:
        logger.error(f"❌ 未知错误：{e}", exc_info=True)
        print(f"❌ 未知错误：{e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()
            logger.info("🔌 数据库连接已关闭")

def export_all_json(output_jsonl=False, jsonl_filename=None):
    """导出所有数据为 JSON 格式"""
    logger.info("📦 开始导出所有数据")
    
    try:
        conn = sqlite3.connect(DB_FILE)
        logger.info(f"✅ 成功连接数据库 {DB_FILE}")
        
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM item_details")
        logger.info("📝 执行 SQL: SELECT * FROM item_details")
        
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        
        logger.info(f"✅ 共获取 {len(rows)} 条数据")
        
        results = []
        for row in rows:
            result = {}
            for i, col in enumerate(columns):
                result[col] = row[i]
            results.append(result)
        
        # 输出 JSON 格式
        output_file_json = "all_products.json"
        logger.info(f"📤 开始输出所有数据到 JSON 文件: {output_file_json}")
        with open(output_file_json, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.info(f"✅ 成功输出数据到 {output_file_json}")
        
        # 如果需要输出为 jsonl
        if output_jsonl:
            output_file = jsonl_filename or f"query_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
            logger.info(f"📤 开始输出所有数据到 JSONL 文件: {output_file}")
            with open(output_file, 'w', encoding='utf-8') as f:
                for result in results:
                    f.write(json.dumps(result, ensure_ascii=False) + '\n')
            logger.info(f"✅ 成功输出数据到 {output_file}")
        
        print(f"✅ 已导出 {len(results)} 条数据到 {output_file_json}")
        if output_jsonl:
            print(f"✅ 同时已导出到 {output_file}")
        
    except sqlite3.OperationalError as e:
        logger.error(f"❌ 数据库错误：{e}")
        print(f"❌ 数据库错误：{e}")
    except Exception as e:
        logger.error(f"❌ 未知错误：{e}", exc_info=True)
        print(f"❌ 未知错误：{e}")
    finally:
        if 'conn' in locals():
            conn.close()
            logger.info("🔌 数据库连接已关闭")

def show_help():
    """显示帮助信息"""
    help_text = '''
📦 商品查询工具（按 itemid 查询）

使用方法：
  python query_lefox.py <itemid>                          # 查询单个 itemid
  python query_lefox.py <itemid> --jsonl [filename]        # 查询单个 itemid 并输出为 JSONL
  python query_lefox.py --batch <id1>,<id2>,...            # 批量查询
  python query_lefox.py --batch <id1>,<id2>,... --jsonl [filename] # 批量查询并输出为 JSONL
  python query_lefox.py --export-all                        # 导出所有数据为 JSON
  python query_lefox.py --export-all --jsonl [filename]     # 导出所有数据为 JSON 和 JSONL
  python query_lefox.py --help                              # 显示帮助信息

--jsonl 参数说明：
  --jsonl              # 输出为 JSONL 文件，文件名自动生成
  --jsonl myfile.jsonl # 输出为 JSONL 文件，指定文件名

示例：
  python query_lefox.py 12345
  python query_lefox.py 12345 --jsonl
  python query_lefox.py 12345 --jsonl output.jsonl
  python query_lefox.py --batch 12345,67890,54321
  python query_lefox.py --batch 12345,67890,54321 --jsonl
  python query_lefox.py --export-all
  python query_lefox.py --export-all --jsonl all_data.jsonl
'''
    print(help_text)

def main():
    parser = argparse.ArgumentParser(
        description='商品查询工具（按 itemid 查询）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
--jsonl 参数说明：
  --jsonl              # 输出为 JSONL 文件，文件名自动生成
  --jsonl myfile.jsonl # 输出为 JSONL 文件，指定文件名

示例：
  python query_lefox.py 12345
  python query_lefox.py 12345 --jsonl
  python query_lefox.py 12345 --jsonl output.jsonl
  python query_lefox.py --batch 12345,67890,54321
  python query_lefox.py --batch 12345,67890,54321 --jsonl
  python query_lefox.py --export-all
  python query_lefox.py --export-all --jsonl all_data.jsonl
'''
    )
    parser.add_argument('itemid', nargs='?', help='要查询的单个 itemid')
    parser.add_argument('--batch', help='批量查询，多个 itemid 用逗号分隔')
    parser.add_argument('--export-all', action='store_true', help='导出所有数据')
    parser.add_argument('--jsonl', nargs='?', const=True, help='输出为 JSONL 文件，可选指定文件名')
    
    args = parser.parse_args()
    
    # 解析 jsonl 参数
    output_jsonl = False
    jsonl_filename = None
    if args.jsonl is True:
        output_jsonl = True
    elif args.jsonl is not None:
        output_jsonl = True
        jsonl_filename = args.jsonl
    
    if args.export_all:
        logger.info("🚀 执行 --export-all 命令")
        export_all_json(output_jsonl=output_jsonl, jsonl_filename=jsonl_filename)
    elif args.batch:
        logger.info("🚀 执行 --batch 命令")
        itemids = [x.strip() for x in args.batch.split(',') if x.strip()]
        if not itemids:
            print("❌ 请提供要查询的 itemid 列表，用逗号分隔")
            logger.error("❌ 未提供有效的 itemid 列表")
            return
        batch_query_by_itemid(itemids, output_jsonl=output_jsonl, jsonl_filename=jsonl_filename)
    elif args.itemid:
        logger.info("🚀 执行单个查询命令")
        query_by_itemid(args.itemid, output_jsonl=output_jsonl, jsonl_filename=jsonl_filename)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()

    