from customers.models import Customer
for val in ["BTES","ITCA","ANDIRA","ITANBARACA","S BTES","BTS","ABATIA"]:
    n = Customer.objects.filter(city=val).count()
    print(f"{n:>6}  {val!r}")
