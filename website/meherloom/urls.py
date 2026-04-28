from django.urls import path

from .views import import_product, index, product_detail, shop


app_name = "meherloom"

urlpatterns = [
    path("", index, name="index"),
    path("shop/", shop, name="shop"),
    path("dashboard/import-product/", import_product, name="import_product"),
    path("products/<int:pk>/", product_detail, name="product_detail"),
]
