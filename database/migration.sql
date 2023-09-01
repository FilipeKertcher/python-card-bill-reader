CREATE TABLE places (
	id serial4 NOT NULL,
	"name" varchar(200) NULL,
	CONSTRAINT places_pkey PRIMARY KEY (id)
);

CREATE TABLE place_tags (
	id serial4 NOT NULL,
	place_id int4 NULL,
    tag varchar(200) NOT NULL
	CONSTRAINT place_tags_pkey PRIMARY KEY (id),
	CONSTRAINT fk_place FOREIGN KEY (place_id) REFERENCES places(id)
);