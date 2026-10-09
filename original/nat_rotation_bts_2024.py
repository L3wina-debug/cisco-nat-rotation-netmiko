# Original BTS project script (2024), kept as written in the project report.
# Only change: the router credentials were replaced with placeholders.
# Known issues are fixed in nat_rotator.py (see README, "Fixes").
import ipaddress
import os
import time
from netmiko import ConnectHandler


def div_et_enreg_ips(adresse_reseau):
    try:
        net = ipaddress.ip_network(adresse_reseau, strict=False)
        with open('ip_pub.txt', 'w') as f:
            for ip in net.hosts():
                f.write(str(ip) + '\n')
        print(f"Adresses IP utilisables enregistrées dans 'ip_pub.txt'")
    except Exception as e:
        print(f"Erreur: {str(e)}")


def suppr_pool_NAT(R1):
    try:
        net_connect = ConnectHandler(**R1)

        clear_commands = [
            'claire traduction ip nat  *'
        ]
        output = net_connect.send_command_timing(clear_commands[0])
        print("NAT translations Effacee.")

        check_command = "show run | include ip nat pool NET"
        output = net_connect.send_command(check_command)

        if 'ip nat pool NET' in output:
            print("Suppression du pool NAT existant 'nat'...")

            remove_commands = [
                'conf t',
                'no ip nat pool nat',
                'exit'
            ]
            output = net_connect.send_config_set(remove_commands)
            print(output)
            print("Le pool NAT 'nat' a été supprimé avec succés.")
        else:
            print("Aucun pool NAT 'nat' existant n'a été trouvé. Procédure en cours...")

        net_connect.disconnect()

    except Exception as e:
        print(f"Erreur lors de la suppression du pool NAT: {str(e)}")


def change_nat_address(R1, nat_address):
    try:
        net_connect = ConnectHandler(**R1, global_delay_factor=2)

        nat_commands = [
            'conf t',
            f'ip nat pool NET {nat_address} {nat_address} netmask 255.255.255.0',
            'exit'
        ]

        output = net_connect.send_config_set(nat_commands)
        print(output)

        net_connect.disconnect()

        print(f"L'adresse NAT a été modifiée en {nat_address}")
    except Exception as e:
        print(f"erreur lors du changement d'adresse NAT: {str(e)}")


def tester_internet_connectivite():
    ip = "192.168.175.2"
    response = os.system(f"ping -c 3 {ip} > /dev/null 2>&1")
    return response == 0


def enreg_ip_utilise(ip_address):
    with open('ip_util.txt', 'a') as f:
        f.write(ip_address + '\n')


if __name__ == "__main__":
    R1 = {
        'device_type': 'cisco_ios',
        'host': '192.168.174.1',
        'username': '<USERNAME>',
        'password': '<PASSWORD>',
        'timeout': 120,
        'global_delay_factor': 2
    }

    adresse_reseau = input("Entrez l'adresse réseau avec la notation CIDR (e.g., '100.10.10.0/24'): ")

    div_et_enreg_ips(adresse_reseau)

    suppr_pool_NAT(R1)

    with open('ip_pub.txt', 'r') as f:
        ip_addresses = f.readlines()

    actuel_ip_indice = 0

    while True:
        adresse_NAT_actuel1 = ip_addresses[actuel_ip_indice].strip()
        actuel_ip_indice = (actuel_ip_indice + 1) % len(ip_addresses)
        adresse_NAT_actuel2 = ip_addresses[actuel_ip_indice].strip()

        if not tester_internet_connectivite():
            print("La connexion Internet est en panne. Changer l'adresse NAT...")

            try_count = 0
            max_attempts = 5
            connected = False

            while try_count < max_attempts and not connected:
                try_count += 1
                print(f"Établissement de la connexion... Tentative {try_count}")

                try:
                    suppr_pool_NAT(R1)
                    change_nat_address(R1, adresse_NAT_actuel1, adresse_NAT_actuel2)
                    enreg_ip_utilise(adresse_NAT_actuel1)
                    enreg_ip_utilise(adresse_NAT_actuel2)
                    connected = True
                except Exception as e:
                    print(f"Nouvelle tentative de connexion... ({try_count}/{max_attempts})")
                    print(f"Erreur inattendue: {str(e)}")

                time.sleep(30)

            if not connected:
                print("Échec de l'établissement de la connexion après les tentatives.")

        else:
            print("La connexion Internet est active.")
            time.sleep(180)

        if actuel_ip_indice == len(ip_addresses) - 1:
            adresse_reseau = input("Toutes les IP utilisées. Entrez une nouvelle adresse réseau avec la notation CIDR: ")

            div_et_enreg_ips(adresse_reseau)

            suppr_pool_NAT(R1)

            with open('ip_pub.txt', 'r') as f:
                ip_addresses = f.readlines()

            actuel_ip_indice = 0
