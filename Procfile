web: bash -c "flask --app app db upgrade && flask --app app init-user && gunicorn 'app:create_app()' --bind 0.0.0.0:$PORT --workers 2 --timeout 120"
