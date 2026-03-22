This application uses docker for deployment when we are in prod.  To do a redeployment we'll need to do

`docker compose stop && docker compose build && docker compose up -d` 
