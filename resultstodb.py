import dbops
import csv
import argparse
import ConfigParser
import logging
import os
import calendar
import datetime

def _parse_args():
    parser = argparse.ArgumentParser(description='Put parsed AppCensus test results into the database')
    parser.add_argument('dbcreds', help='Path to a file containing database connection details and credentials')
    parser.add_argument('--packetfile', help='Path to the file generated by the Reardon Packet Parser')
    parser.add_argument('--permfile', help='Path to the file generated by the Reardon Permission Parser')
    parser.add_argument('--test', action='store_true', help='Do not push to DB')
    parser.add_argument('--verbose', '-v', action='store_true', help='Display verbose logging')

    return parser.parse_args()

def _parse_creds(creds_file):
    config = ConfigParser.ConfigParser()
    config.read(creds_file)

    # Get the database login
    database_header = 'Database'
    db_cred = None
    if database_header in config.sections():
        db_cred = {'host':config.get(database_header, 'host'), \
                   'database':config.get(database_header, 'database'), \
                   'user':config.get(database_header, 'user'), \
                   'password':config.get(database_header, 'password')}
        logging.info('Found database credentials for host=%s, database=%s, user=%s' % (db_cred['host'], db_cred['database'], db_cred['user']))

    return db_cred

def _enable_verbose():
    logging.basicConfig(level=logging.DEBUG)
    logging.info('Enabled INFO level verbose logging')

def _init_db(creds_file):
    db_creds = _parse_creds(creds_file)
    dbops.init(db_creds['host'], db_creds['database'], db_creds['user'], db_creds['password'])

    logging.info('Connected to DB host %s database %s as user %s' % (db_creds['host'], db_creds['database'], db_creds['user']))

def _log_date_to_timestamp(log_date):
    # Example: 06-06 15:39:47.707

    # If the month-day is in the future, assume the previous year, otherwise assume the current year
    current = datetime.datetime.utcnow()
    parsed = datetime.datetime.strptime(log_date, '%m-%d %H:%M:%S.%f')
    parsed = parsed.replace(year=current.year, tzinfo=current.tzinfo)

    if(parsed > current):
        parsed = parsed.replace(year=current.year - 1)

    logging.info('Parsed %s as datetime %s' % (log_date, str(parsed)))

    # Convert to seconds from epoch
    timestamp = calendar.timegm(parsed.utctimetuple())

    logging.info('Parsed %s as timestamp %d from epoch' % (log_date, timestamp))

    return timestamp

_last_package = None
_last_version = None
_last_release_id = None
def _get_release_id(package_name, version_code):
    global _last_package, _last_version, _last_release_id

    if(package_name != _last_package or version_code != _last_version):
        _last_package = package_name
        _last_version = version_code
        release_id = dbops.get_release_id(package_name, version_code)

        assert release_id is not None, 'App-version %s-%d was not found in the database' % (package_name, version_code)
        _last_release_id = release_id
        logging.info('For app/version %s-%d, found release ID %d in DB' % (package_name, version_code, _last_release_id))

    return _last_release_id

def read_packets(packet_file, test=False):
    assert os.path.isfile(packet_file), 'Input packet file %s does not exist' % packet_file
    EXPECTED_COLUMNS = 10

    with open(packet_file, 'r') as fh:
        reader = csv.reader(fh)
        for row in reader:
            # packageName | versionCode | domain | tlsSNI | ipAddress | port | isTLS | dataType | blob | timestamp
            assert len(row) == EXPECTED_COLUMNS, 'Expected %d columns, got %d (%s)' % (EXPECTED_COLUMNS, len(row), str(row))

            try: 
                package_name = row[0]
                version_code = int(row[1])
                domain = row[2]
                tls_sni = row[3]
                ip_address = row[4]
                port = int(row[5])
                is_tls = int(row[6])
                data_type = row[7]
                blob = row[8]
                timestamp = _log_date_to_timestamp(row[9])

                logging.debug('package_name=%s, version_code=%d, domain=%s, tls_sni=%s, ip_address=%s, port=%d, is_tls=%d, data_type=%s, blob=%s, timestamp=%d' % \
                              (package_name, version_code, domain, tls_sni, ip_address, port, is_tls, data_type, blob, timestamp))

                release_id = _get_release_id(package_name, version_code)

                if(not test):
                    dbops.insert_transmission(release_id, data_type, timestamp, \
                                              domain=domain, tls_sni=tls_sni, ip_address=ip_address, port=port, is_tls=is_tls, payload=blob)
            except ValueError as e:
                logging.error('ValueError for row %s, skipping' % str(row))
                logging.exception(e)

                continue

def read_perms(perms_file, test=False):
    assert os.path.isfile(perms_file), 'Input permissions file %s does not exist' % perms_file
    EXPECTED_COLUMNS = 5

    with open(perms_file, 'r') as fh:
        reader = csv.reader(fh)
        for row in reader:
            # com.yelp.android,19013603,WRITE_EXTERNAL_STORAGE,0,0
            # com.yelp.android,19013603,GET_ACCOUNTS,1,06-08 18:42:39.285
            assert len(row) == EXPECTED_COLUMNS, 'Expected %d columns, got %d (%s)' % (EXPECTED_COLUMNS, len(row), str(row))

            try:
                package_name = row[0]
                version_code = int(row[1])
                permission = row[2]
                is_used = int(row[3])
                timestamp = _log_date_to_timestamp(row[4]) if is_used == 1 else 0

                logging.debug('package_name=%s, version_code=%d, permission=%s' % (package_name, version_code, permission))

                release_id = _get_release_id(package_name, version_code)

                if(not test):
                    dbops.insert_permission(release_id, permission, timestamp, is_used=is_used)
            except ValueError as e:
                logging.error('ValueError for row %s, skipping' % str(row))
                logging.exception(e)

                continue

if __name__ == '__main__':
    args = _parse_args()

    if(args.verbose):
        _enable_verbose()

    _init_db(args.dbcreds)

    packet_file = args.packetfile
    if(packet_file is not None):
        read_packets(packet_file, test=args.test)
    
    perm_file = args.permfile
    if(perm_file is not None):
        read_perms(perm_file, test=args.test)

