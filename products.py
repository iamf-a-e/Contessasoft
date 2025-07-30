class Product:
    def __init__(self, name, price, description="", stock=0):
        self.name = name
        self.price = price
        self.description = description
        self.stock = stock
        self.active = stock > 0  # Automatically inactive if stock is 0

    def is_available(self):
        return self.stock > 0 and self.active
        

class Category:
    def __init__(self, name):
        self.name = name
        self.products = []

    def add_product(self, product):
        self.products.append(product)
