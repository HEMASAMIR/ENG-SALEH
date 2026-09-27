-- ### Roles Seed Data ###
INSERT INTO roles (role_name, description) VALUES ('root', 'Highest access role. root users have unrestricted access to all system features, settings, and data, can manage administrators, configure system-wide settings, and can perform critical actions.');
INSERT INTO roles (role_name, description) VALUES ('administrator', 'Elevated access role. Administrators can manage users, content, and operational settings. Admins have broad control but cannot modify core system configurations reserved for root users.');
INSERT INTO roles (role_name, description) VALUES ('supervisor', 'Medium access role. Supervisors can manage recipes and view reports but cannot manage users.');
INSERT INTO roles (role_name, description) VALUES ('operator', 'Standard access role. Operators have limited access based on assigned permissions. Operators can use core features but cannot manage other users or change system settings.');

-- ### Permissions Seed Data ###
INSERT INTO permissions (permission_name, description) VALUES ('CREATE USER', 'The ability to create new users for the system.'); -- 1
INSERT INTO permissions (permission_name, description) VALUES ('EDIT USER', 'The ability to edit existing users for the system.'); -- 2
INSERT INTO permissions (permission_name, description) VALUES ('DELETE USER', 'The ability to delete existing users for the system.'); -- 3
INSERT INTO permissions (permission_name, description) VALUES ('SHOW USER', 'The ability to see existing users for the system.'); -- 4
INSERT INTO permissions (permission_name, description) VALUES ('GIVE PERMISSIONS', 'The ability to give other users permissions for system features.'); -- 5
INSERT INTO permissions (permission_name, description) VALUES ('CHANGE TIME', 'The ability to change systems current time.'); -- 6
INSERT INTO permissions (permission_name, description) VALUES ('SHOW RECIPE', 'The ability to see machine current recipe.'); -- 7
INSERT INTO permissions (permission_name, description) VALUES ('EDIT RECIPE', 'The ability to edit machine current recipe parameters.'); -- 8
INSERT INTO permissions (permission_name, description) VALUES ('START MACHINE', 'The ability to start stopped machine.'); -- 9
INSERT INTO permissions (permission_name, description) VALUES ('STOP MACHINE', 'The ability to stop working machine.'); -- 10
INSERT INTO permissions (permission_name, description) VALUES ('CREATE BACKUP', 'The ability to create backup for the database.'); -- 11
INSERT INTO permissions (permission_name, description) VALUES ('DEPLOY BACKUP', 'The ability to use old backups on the system.'); -- 12
INSERT INTO permissions (permission_name, description) VALUES ('SEND BACKUP', 'The ability to share backups to other devices.'); -- 13
INSERT INTO permissions (permission_name, description) VALUES ('SHOW RECIPE REPORTS', 'The ability to display exported recipe reports.'); -- 14
INSERT INTO permissions (permission_name, description) VALUES ('CREATE RECIPE REPORTS', 'The ability to create recipe reports.'); -- 15
INSERT INTO permissions (permission_name, description) VALUES ('SHOW AUDIT REPORTS', 'The ability to display exported audit reports.'); -- 16
INSERT INTO permissions (permission_name, description) VALUES ('CREATE AUDIT REPORTS', 'The ability to create audit reports.'); -- 17

-- ### Role_Permission Seed Data ###

-- root user permissions
INSERT INTO role_permission (role_id, permission_id) VALUES (1, 1);
INSERT INTO role_permission (role_id, permission_id) VALUES (1, 2);
INSERT INTO role_permission (role_id, permission_id) VALUES (1, 3);
INSERT INTO role_permission (role_id, permission_id) VALUES (1, 4);
INSERT INTO role_permission (role_id, permission_id) VALUES (1, 5);
INSERT INTO role_permission (role_id, permission_id) VALUES (1, 6);
INSERT INTO role_permission (role_id, permission_id) VALUES (1, 7);
INSERT INTO role_permission (role_id, permission_id) VALUES (1, 8);
INSERT INTO role_permission (role_id, permission_id) VALUES (1, 9);
INSERT INTO role_permission (role_id, permission_id) VALUES (1, 10);
INSERT INTO role_permission (role_id, permission_id) VALUES (1, 11);
INSERT INTO role_permission (role_id, permission_id) VALUES (1, 12);
INSERT INTO role_permission (role_id, permission_id) VALUES (1, 13);
INSERT INTO role_permission (role_id, permission_id) VALUES (1, 14);
INSERT INTO role_permission (role_id, permission_id) VALUES (1, 15);
INSERT INTO role_permission (role_id, permission_id) VALUES (1, 16);
INSERT INTO role_permission (role_id, permission_id) VALUES (1, 17);

-- administrator permissions (ID 2)
INSERT INTO role_permission (role_id, permission_id) VALUES (2, 7);
INSERT INTO role_permission (role_id, permission_id) VALUES (2, 8);
INSERT INTO role_permission (role_id, permission_id) VALUES (2, 9);
INSERT INTO role_permission (role_id, permission_id) VALUES (2, 10);
INSERT INTO role_permission (role_id, permission_id) VALUES (2, 11);
INSERT INTO role_permission (role_id, permission_id) VALUES (2, 13);
INSERT INTO role_permission (role_id, permission_id) VALUES (2, 14);
INSERT INTO role_permission (role_id, permission_id) VALUES (2, 15);
INSERT INTO role_permission (role_id, permission_id) VALUES (2, 16);
INSERT INTO role_permission (role_id, permission_id) VALUES (2, 17);

-- supervisor permissions (ID 3)
INSERT INTO role_permission (role_id, permission_id) VALUES (3, 7);
INSERT INTO role_permission (role_id, permission_id) VALUES (3, 8);
INSERT INTO role_permission (role_id, permission_id) VALUES (3, 14);
INSERT INTO role_permission (role_id, permission_id) VALUES (3, 16);

-- operator permissions (ID 4)
INSERT INTO role_permission (role_id, permission_id) VALUES (4, 7);
INSERT INTO role_permission (role_id, permission_id) VALUES (4, 14);
INSERT INTO role_permission (role_id, permission_id) VALUES (4, 16);


--- ### Users

INSERT INTO users (user_name, password_hash, role_id, full_name, is_disabled, is_deleted) 
VALUES ('root', crypt('root', gen_salt('bf')), 1, 'Root User', FALSE, FALSE);

INSERT INTO users (user_name, password_hash, role_id, full_name, is_disabled, is_deleted) 
VALUES ('administrator', crypt('administrator', gen_salt('bf')), 2, 'Administrator User', FALSE, FALSE);

INSERT INTO users (user_name, password_hash, role_id, full_name, is_disabled, is_deleted) 
VALUES ('Engsalehalenbawi@gmail', crypt('Sa_30415018053', gen_salt('bf')), 2, 'Saleh Alenbawi', FALSE, FALSE);

INSERT INTO users (user_name, password_hash, role_id, full_name, is_disabled, is_deleted) 
VALUES ('supervisor', crypt('supervisor', gen_salt('bf')), 3, 'Supervisor User', FALSE, FALSE);

INSERT INTO users (user_name, password_hash, role_id, full_name, is_disabled, is_deleted) 
VALUES ('operator', crypt('operator', gen_salt('bf')), 4, 'Operator User', FALSE, FALSE);