# cPanel Deployment Notes

This project is prepared to work on shared hosting without depending on a permanent Celery worker.

## Recommended scheduler

Use a cPanel cron job every 5 minutes:

```bash
/home/USERNAME/virtualenv/YOUR_APP_PATH/3.11/bin/python /home/USERNAME/YOUR_APP_PATH/manage.py sync_source_stock
```

If you also want a deeper refresh of descriptions and images from source sites, run a second cron less often:

```bash
/home/USERNAME/virtualenv/YOUR_APP_PATH/3.11/bin/python /home/USERNAME/YOUR_APP_PATH/manage.py sync_source_stock --refresh-details
```

## Product import

You can import a source product from the shell:

```bash
python manage.py import_source_product BRAND_ID "https://mother-brand.com/product-page" 149.00
```

You can also add a product in Django admin by saving:

- `brand`
- `source_url`
- `manual_price`

The admin will try to fetch the mother-brand details on first save.

## Environment variables

Recommended env vars for production:

```bash
DJANGO_SECRET_KEY=change-me
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
DB_ENGINE=mysql
DB_NAME=cpanel_db_name
DB_USER=cpanel_db_user
DB_PASSWORD=cpanel_db_password
DB_HOST=localhost
DB_PORT=3306
```

## Celery note

Celery task hooks are included in the app, but shared hosting often does not keep long-running workers alive consistently.

Default recommendation:

- use cron for `sync_source_stock`
- treat Celery as optional, only if your hosting plan reliably supports workers
