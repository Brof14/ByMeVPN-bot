"""
Импорт пользователей из CSV файла в базу данных SQLite.
Запустить один раз для импорта старой базы пользователей.
"""
import asyncio
import csv
import logging
from datetime import datetime

from database import get_db, init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def import_users_from_csv(csv_file: str = "bymevpn_users_20260402_0506.csv"):
    """Импортировать пользователей из CSV в базу данных."""
    
    # Инициализируем базу данных
    await init_db()
    db = await get_db()
    
    imported_count = 0
    skipped_count = 0
    
    try:
        with open(csv_file, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            
            for row in reader:
                try:
                    user_id = int(row['user_id'])
                    trial_used = int(row['trial_used']) if row['trial_used'] else 0
                    referrer_id = int(row['referrer_id']) if row['referrer_id'] else None
                    registered = row['registered']  # формат YYYY-MM-DD
                    total_keys = int(row['total_keys']) if row['total_keys'] else 0
                    active_keys = int(row['active_keys']) if row['active_keys'] else 0
                    total_paid = int(row['total_paid_rub']) if row['total_paid_rub'] else 0
                    
                    # Конвертируем дату в timestamp
                    if registered:
                        try:
                            dt = datetime.strptime(registered, '%Y-%m-%d')
                            created_timestamp = int(dt.timestamp())
                        except ValueError:
                            created_timestamp = int(datetime.now().timestamp())
                    else:
                        created_timestamp = int(datetime.now().timestamp())
                    
                    # Проверяем существует ли пользователь
                    existing = await db.execute(
                        "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
                    )
                    existing = await existing.fetchone()
                    
                    if existing:
                        logger.info(f"User {user_id} already exists, skipping...")
                        skipped_count += 1
                        continue
                    
                    # Вставляем пользователя
                    await db.execute(
                        """
                        INSERT INTO users (user_id, referrer_id, trial_used, total_paid, created)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (user_id, referrer_id, trial_used, total_paid, created_timestamp)
                    )
                    
                    imported_count += 1
                    logger.info(f"Imported user {user_id} (trial_used={trial_used}, referrer={referrer_id})")
                    
                except Exception as e:
                    logger.error(f"Error importing row {row}: {e}")
                    skipped_count += 1
            
            await db.commit()
            
    except FileNotFoundError:
        logger.error(f"CSV file {csv_file} not found!")
        return
    except Exception as e:
        logger.error(f"Error reading CSV file: {e}")
        return
    
    logger.info(f"Import completed: {imported_count} imported, {skipped_count} skipped")

async def main():
    """Главная функция."""
    logger.info("Starting user import from CSV...")
    await import_users_from_csv()
    logger.info("Import finished!")

if __name__ == "__main__":
    asyncio.run(main())
