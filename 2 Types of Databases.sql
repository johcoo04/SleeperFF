2 Types of Databases
- Relational - where you store data based on the specific type of object, this is where you use to query or modify the language
    - Popular variations is MySQL, SQL Server or Oracle, but all use similar syntax and you can switch between them easily

Non Relational
- Dont have tables or relationships, don't understand SQL, they have their own query language

- SQL isn't case sensitive, but best practice is key words is UPPERCASE and everything else is lowercase
Select Statement
- To grab the database when you create your queries


USE sql_store (selects SQL database, to run queries on)
SELECT <what columns do you want to retrieve>
FROM <what table do you want to grab>

Example
USE sql_store; --database

-- SELECT *
-- FROM customers
-- WHERE customer_id = 1 # only get customer with id of 1
-- ORDER BY first_name # sorted based on first name, so 'A' to 'Z'

Order of the columns matter it should always go

SELECT 
FROM --table
WHERE
ORDER BY

Or you could do it all on one line 

SELECT * FROM customers WHERE customer_id = 1 ORDER BY first_name

instead if you want to just get the columns you can specify that instead of * which just states 'grab all columns'

SELECT first_name, last_name --it is order specific
FROM customers

SELECT last_name, first_name --displays the last_name column first instead
from customers

if the columns are getting too long you can split it up onto new lines

SELECT 
    last_name,
    first_name,
    points,
    points + 10
FROM customers

you can give columns aliases to rename the columns in the result sset with the AS keyword

SELECT 
    last_name,
    first_name,
    points,
    points + 10 AS discount_factor --column name is discount_factor
FROM customers

or instead if you wanted to have a space in the column factor you would need 'quotes'

SELECT 
    last_name,
    first_name,
    points,
    points + 10 AS 'discount factor' -- column name is discount factor
FROM customers

SELECT state
FROM customers

this query returns all the states of all the customers, but lets say their was a duplicate
if that is not what you want but instead just want unique values then you use DISTINCT

SELECT DISTINCT state
FROM customers

SELECT 
    name, 
    unit_price, 
    unit_price * 1.1 AS 'new price'
FROM products

The WHERE clause will serve as a filter or an if statement, it will evaluate the data and check if true and then return if so
SELECT * 
FROM customers
WHERE > 3000 -- this query will return customers who have 3000 or more points

SELECT * 
FROM customers
WHERE state = 'VA'

SELECT * 
FROM customers
WHERE birth_date > '1990-01-01' --MySQL stores data in YYYY-MM-DD

SELECT *
FROM orders
WHERE order_date >= '2025-01-01' --Return all the orders from 2025, how do I make it modular and say all orders placed from this year?

How to combine multiple conditions when filtering Data? 



