import sqlite3
import os

print("Buscando banco de dados...")
db_file = None

# Procura o banco de dados principal na pasta data/
if os.path.exists('data'):
    for f in os.listdir('data'):
        if f.endswith('.db') and 'celery' not in f:
            db_file = os.path.join('data', f)
            break

if db_file:
    print(f"Banco encontrado: {db_file}")
    conn = sqlite3.connect(db_file)
    c = conn.cursor()
    
    # Zera as contagens e limpa as sessões ativas
    queries = [
        "UPDATE users SET active_sessions = 0",
        "DELETE FROM active_sessions"
    ]
    
    for query in queries:
        try:
            c.execute(query)
            print(f"Executado com sucesso: {query}")
        except sqlite3.OperationalError:
            pass # Ignora se a tabela tiver um nome ligeiramente diferente
            
    conn.commit()
    conn.close()
    print("✅ Bloqueio derrubado! Pode atualizar a página e fazer login.")
else:
    print("❌ Banco de dados não encontrado na pasta data/.")