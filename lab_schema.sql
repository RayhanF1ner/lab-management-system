-- =====================================================================
--  Computer Lab Management System - MySQL schema
--  Run:  mysql -u root -p < lab_schema.sql
-- =====================================================================
CREATE DATABASE IF NOT EXISTS computer_lab;
USE computer_lab;

-- ---------- Tables ----------------------------------------------------
CREATE TABLE IF NOT EXISTS lab (
    Lab_ID      INT AUTO_INCREMENT PRIMARY KEY,
    Name        VARCHAR(100) NOT NULL,
    Building    VARCHAR(100),
    Room_No     VARCHAR(20)
);

CREATE TABLE IF NOT EXISTS vendor (
    Vendor_ID       INT AUTO_INCREMENT PRIMARY KEY,
    Name            VARCHAR(100) NOT NULL,
    Contact_Email   VARCHAR(100),
    Phone           VARCHAR(20),
    City            VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS lab_user (
    User_ID     INT AUTO_INCREMENT PRIMARY KEY,
    Name        VARCHAR(100) NOT NULL,
    Email       VARCHAR(100),
    Role        ENUM('Student', 'Faculty', 'Staff') NOT NULL DEFAULT 'Student',
    Department  VARCHAR(100)
);

CREATE TABLE IF NOT EXISTS staff (
    Staff_ID    INT AUTO_INCREMENT PRIMARY KEY,
    Name        VARCHAR(100) NOT NULL,
    Role        VARCHAR(50),
    Contact     VARCHAR(50),
    Lab_ID      INT,
    FOREIGN KEY (Lab_ID) REFERENCES lab(Lab_ID) ON DELETE SET NULL
);

-- Equipment catalogue (one row per equipment type / tag)
CREATE TABLE IF NOT EXISTS asset (
    Asset_Tag   VARCHAR(30) PRIMARY KEY,
    Asset_Name  VARCHAR(100) NOT NULL,
    Category    VARCHAR(30)  NOT NULL,
    Brand       VARCHAR(50),
    Model       VARCHAR(50),
    Specs       VARCHAR(255),
    Vendor_ID   INT,
    Lab_ID      INT,
    FOREIGN KEY (Vendor_ID) REFERENCES vendor(Vendor_ID) ON DELETE SET NULL,
    FOREIGN KEY (Lab_ID)    REFERENCES lab(Lab_ID)       ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS asset_stock (
    Asset_Tag         VARCHAR(30) PRIMARY KEY,
    Total_Quantity    INT NOT NULL,
    Number_Available  INT NOT NULL,
    FOREIGN KEY (Asset_Tag) REFERENCES asset(Asset_Tag) ON DELETE CASCADE
);

-- Equipment loans (laptops, projectors, cables, VR kits, ...)
CREATE TABLE IF NOT EXISTS checkout (
    Checkout_ID     INT AUTO_INCREMENT PRIMARY KEY,
    Asset_Tag       VARCHAR(30) NOT NULL,
    User_ID         INT NOT NULL,
    Issue_Date      DATE NOT NULL,
    Due_Date        DATE NOT NULL,
    Return_Date     DATE NULL,
    Fine            DECIMAL(8,2) NOT NULL DEFAULT 0,
    Condition_Notes VARCHAR(255),
    FOREIGN KEY (Asset_Tag) REFERENCES asset(Asset_Tag),
    FOREIGN KEY (User_ID)   REFERENCES lab_user(User_ID)
);

-- Lab PCs / seats
CREATE TABLE IF NOT EXISTS workstation (
    Workstation_ID  INT AUTO_INCREMENT PRIMARY KEY,
    Lab_ID          INT NOT NULL,
    Name            VARCHAR(30) NOT NULL,
    Specs           VARCHAR(255),
    Status          ENUM('Available', 'In Use', 'Maintenance') NOT NULL DEFAULT 'Available',
    UNIQUE (Lab_ID, Name),
    FOREIGN KEY (Lab_ID) REFERENCES lab(Lab_ID) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS session_log (
    Session_ID      INT AUTO_INCREMENT PRIMARY KEY,
    Workstation_ID  INT NOT NULL,
    User_ID         INT NOT NULL,
    Start_Time      DATETIME NOT NULL,
    End_Time        DATETIME NULL,
    FOREIGN KEY (Workstation_ID) REFERENCES workstation(Workstation_ID) ON DELETE CASCADE,
    FOREIGN KEY (User_ID)        REFERENCES lab_user(User_ID)
);

CREATE TABLE IF NOT EXISTS maintenance_ticket (
    Ticket_ID       INT AUTO_INCREMENT PRIMARY KEY,
    Workstation_ID  INT NULL,
    Reported_By     INT NULL,
    Issue           VARCHAR(500) NOT NULL,
    Status          ENUM('Open', 'Resolved') NOT NULL DEFAULT 'Open',
    Reported_At     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    Resolved_At     DATETIME NULL,
    FOREIGN KEY (Workstation_ID) REFERENCES workstation(Workstation_ID) ON DELETE SET NULL,
    FOREIGN KEY (Reported_By)    REFERENCES lab_user(User_ID)            ON DELETE SET NULL
);

-- ---------- Stored procedures -----------------------------------------
DROP PROCEDURE IF EXISTS AddNewAsset;
DROP PROCEDURE IF EXISTS IssueAsset;
DROP PROCEDURE IF EXISTS ReturnAsset;
DROP PROCEDURE IF EXISTS DeleteAsset;
DROP PROCEDURE IF EXISTS StartSession;
DROP PROCEDURE IF EXISTS EndSession;

DELIMITER $$

-- Add a new equipment type, or add units to an existing tag
CREATE PROCEDURE AddNewAsset(
    IN p_tag VARCHAR(30), IN p_name VARCHAR(100), IN p_category VARCHAR(30),
    IN p_brand VARCHAR(50), IN p_model VARCHAR(50), IN p_specs VARCHAR(255),
    IN p_vendor INT, IN p_lab INT, IN p_qty INT)
BEGIN
    IF p_qty < 1 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Quantity must be at least 1';
    END IF;

    IF EXISTS (SELECT 1 FROM asset WHERE Asset_Tag = p_tag) THEN
        UPDATE asset_stock
           SET Total_Quantity   = Total_Quantity + p_qty,
               Number_Available = Number_Available + p_qty
         WHERE Asset_Tag = p_tag;
    ELSE
        INSERT INTO asset (Asset_Tag, Asset_Name, Category, Brand, Model, Specs, Vendor_ID, Lab_ID)
        VALUES (p_tag, p_name, p_category, p_brand, p_model, p_specs, p_vendor, p_lab);
        INSERT INTO asset_stock (Asset_Tag, Total_Quantity, Number_Available)
        VALUES (p_tag, p_qty, p_qty);
    END IF;
END$$

-- Lend equipment to a user for p_days days
CREATE PROCEDURE IssueAsset(IN p_tag VARCHAR(30), IN p_user INT, IN p_days INT)
BEGIN
    DECLARE v_avail INT DEFAULT NULL;

    SELECT Number_Available INTO v_avail
      FROM asset_stock WHERE Asset_Tag = p_tag FOR UPDATE;

    IF v_avail IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Equipment not found';
    ELSEIF v_avail < 1 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'No units of this equipment are available';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM lab_user WHERE User_ID = p_user) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'User not found';
    END IF;

    INSERT INTO checkout (Asset_Tag, User_ID, Issue_Date, Due_Date)
    VALUES (p_tag, p_user, CURDATE(), DATE_ADD(CURDATE(), INTERVAL p_days DAY));

    UPDATE asset_stock SET Number_Available = Number_Available - 1 WHERE Asset_Tag = p_tag;
END$$

-- Return equipment; fine = 10 per overdue day (change the rate here)
CREATE PROCEDURE ReturnAsset(
    IN p_checkout_id INT, IN p_return_date DATE, IN p_condition VARCHAR(255),
    OUT p_fine DECIMAL(8,2))
BEGIN
    DECLARE v_tag VARCHAR(30) DEFAULT NULL;
    DECLARE v_due DATE;
    DECLARE v_returned DATE;

    SELECT Asset_Tag, Due_Date, Return_Date INTO v_tag, v_due, v_returned
      FROM checkout WHERE Checkout_ID = p_checkout_id FOR UPDATE;

    IF v_tag IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Checkout record not found';
    ELSEIF v_returned IS NOT NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This item has already been returned';
    END IF;

    SET p_fine = GREATEST(DATEDIFF(p_return_date, v_due), 0) * 10.00;

    UPDATE checkout
       SET Return_Date = p_return_date, Fine = p_fine, Condition_Notes = p_condition
     WHERE Checkout_ID = p_checkout_id;

    UPDATE asset_stock SET Number_Available = Number_Available + 1 WHERE Asset_Tag = v_tag;
END$$

-- Remove equipment (blocked while any unit is still lent out)
CREATE PROCEDURE DeleteAsset(IN p_tag VARCHAR(30))
BEGIN
    IF NOT EXISTS (SELECT 1 FROM asset WHERE Asset_Tag = p_tag) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Equipment not found';
    END IF;
    IF EXISTS (SELECT 1 FROM checkout WHERE Asset_Tag = p_tag AND Return_Date IS NULL) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Cannot delete: units are still checked out';
    END IF;
    DELETE FROM checkout WHERE Asset_Tag = p_tag;
    DELETE FROM asset WHERE Asset_Tag = p_tag;   -- asset_stock cascades
END$$

-- Log a user onto a workstation
CREATE PROCEDURE StartSession(IN p_ws INT, IN p_user INT)
BEGIN
    DECLARE v_status VARCHAR(20) DEFAULT NULL;

    SELECT Status INTO v_status FROM workstation WHERE Workstation_ID = p_ws FOR UPDATE;

    IF v_status IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Workstation not found';
    ELSEIF v_status <> 'Available' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Workstation is not available';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM lab_user WHERE User_ID = p_user) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'User not found';
    END IF;

    INSERT INTO session_log (Workstation_ID, User_ID, Start_Time) VALUES (p_ws, p_user, NOW());
    UPDATE workstation SET Status = 'In Use' WHERE Workstation_ID = p_ws;
END$$

-- Log a user off a workstation
CREATE PROCEDURE EndSession(IN p_ws INT)
BEGIN
    UPDATE session_log SET End_Time = NOW()
     WHERE Workstation_ID = p_ws AND End_Time IS NULL;

    IF ROW_COUNT() = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'No active session on this workstation';
    END IF;

    UPDATE workstation SET Status = 'Available'
     WHERE Workstation_ID = p_ws AND Status = 'In Use';
END$$

DELIMITER ;

-- ---------- Optional sample data --------------------------------------
INSERT INTO lab (Name, Building, Room_No) VALUES ('Computer Lab 1', 'Main Block', '101');
INSERT INTO vendor (Name, Contact_Email, Phone, City) VALUES ('TechSource Pvt Ltd', 'sales@techsource.example', '9999999999', 'Bengaluru');
INSERT INTO staff (Name, Role, Contact, Lab_ID) VALUES ('Lab Admin', 'Lab Technician', 'admin@example.com', 1);
INSERT INTO workstation (Lab_ID, Name, Specs) VALUES
    (1, 'PC-01', 'i5 / 16GB / 512GB SSD'),
    (1, 'PC-02', 'i5 / 16GB / 512GB SSD'),
    (1, 'PC-03', 'i5 / 16GB / 512GB SSD');