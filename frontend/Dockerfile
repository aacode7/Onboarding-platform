FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
COPY apps ./apps
COPY tsconfig.json ./tsconfig.json
RUN npm ci
RUN npm --workspace apps/dashboard run build && cp -r apps/dashboard/dist /app/dashboard-dist
RUN npm --workspace apps/customers run build && cp -r apps/customers/dist /app/customers-dist
RUN npm --workspace apps/shell run build

FROM nginx:1.27-alpine AS shell
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/apps/shell/dist /usr/share/nginx/html
EXPOSE 80

FROM nginx:1.27-alpine AS remote
COPY --from=build /app/dashboard-dist /usr/share/nginx/html
COPY nginx-remote.conf /etc/nginx/conf.d/default.conf
EXPOSE 80

FROM nginx:1.27-alpine AS customers
COPY --from=build /app/customers-dist /usr/share/nginx/html
COPY nginx-remote.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
