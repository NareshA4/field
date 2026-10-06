from apps.ui.navigation import NavItem, register

register(NavItem("Parts", "inventory:parts", "inventory.part.view", "box", "Inventory", 10, "/app/inventory/parts"))
register(NavItem("Stock", "inventory:stock", "inventory.view", "grid", "Inventory", 20, "/app/inventory/stock"))
register(NavItem("Warehouses", "inventory:warehouses", "inventory.view", "building", "Inventory", 30,
                 "/app/inventory/warehouses"))
register(NavItem("Reservations", "inventory:reservations", "inventory.view", "list-check", "Inventory", 40,
                 "/app/inventory/reservations"))
register(NavItem("Movements", "inventory:movements", "inventory.view", "clipboard", "Inventory", 50,
                 "/app/inventory/movements"))
