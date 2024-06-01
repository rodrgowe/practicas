
install.packages("ggplot2")

library(agricolae)
library(rlang)
library(tidyverse)
library(ggplot2)
library(readxl)
library(tidyr)
library(dplyr)
??ggplot
enoe <- read_excel("C:/Users/ACER/Desktop/TODO PROGRAMACION/R-Rstudio/Archivos/3.- GGPlot 2/mu_enoe.xlsx")

setwd("C:/Users/ACER/Desktop")
summary(enoe)
summary(enoe$ingreso_mensual)
?ggplot2
(ggplot(data = enoe) + geom_point(mapping = aes(x=anios_esc, y = ingreso_mensual), color ="blue"))
?geom_point
(ggplot(enoe) + geom_area(mapping = aes(x=anios_esc, y = ingreso_mensual), color = "pink"))

(ggplot(enoe) + geom_point(mapping = aes(x = anios_esc, y = ingreso_mensual, color = tipo_empleo, shape = tipo_empleo)))
(ggplot(enoe) + geom_point(mapping = aes(x = anios_esc, y = ingreso_mensual, alpha = tipo_empleo)))
## PENSAR EN CAPAS

(ggplot(enoe) + geom_point(aes(anios_esc, ingreso_mensual, color = "blue")) + geom_smooth(aes(anios_esc, ingreso_mensual)))

ggplot(enoe) + geom_point(aes(anios_esc, ingreso_mensual), color = "blue") + geom_smooth(aes(anios_esc, ingreso_mensual), color= "green")

## SEPARAR POR FACETAS facet_wrap

ggplot(enoe) + geom_point(aes(anios_esc, ingreso_mensual), color = "blue") + facet_wrap(~sex, nrow = 1, ncol = 2)
ggplot(enoe) + geom_point(aes(anios_esc, ingreso_mensual, color = tipo_empleo)) + facet_wrap(~sex, nrow = 1, ncol = 2)

ggplot(enoe) + geom_point(aes(anios_esc, ingreso_mensual, color = tipo_empleo)) + geom_smooth(aes(anios_esc, ingreso_mensual), color = "blue") + facet_wrap(~sex, nrow = 1, ncol = 2)

ggplot(enoe) + geom_point(aes(anios_esc, ingreso_mensual, color = tipo_empleo)) + facet_grid(tipo_empleo ~ sex)

#ggplot para separar, tengo que denominar el nrow como filas, "ingreso mensual, y ncol columnas que seria por el ~sex que son 2.
ggplot(enoe) +
  geom_point(aes(anios_esc, ingreso_mensual)) +
  facet_wrap(~sex, nrow = 1, ncol = 2)
#ggplot separado por todo, table para saber cantidad de opciones en cada fila
#
ggplot(enoe) +
  geom_point(aes(anios_esc, ingreso_mensual, color = tipo_empleo)) +
  facet_grid(tipo_empleo ~ sex)

####grafico de lineas 


ggplot(enoe) +
  geom_smooth(aes(anios_esc, ingreso_mensual, linetype=sex))

ggplot(enoe) +
  geom_smooth(aes(anios_esc, ingreso_mensual, linetype=sex)) +
  geom_point(aes(anios_esc, ingreso_mensual, color = tipo_empleo))

ggplot(enoe, mapping = aes(anios_esc, ingreso_mensual)) 
+ geom_point(mapping = aes(color=tipo_empleo)) 
+ geom_smooth(aes(linetype = tipo_empleo))

ggplot(enoe, aes(anios_esc, ingreso_mensual)) +
  geom_point(mapping = aes(color = niv_edu))
ggplot(enoe, aes(anios_esc, ingreso_mensual)) +
  geom_point(mapping = aes(color = niv_edu), show.legend = F)


ggplot(enoe, aes(anios_esc, ingreso_mensual)) + 
  geom_smooth(aes(), show.legend = T) +
  facet_wrap(~sex, nrow = 1, ncol = 2)

ggplot(enoe, aes(anios_esc, ingreso_mensual))

ggplot(enoe) +
  geom_bar(aes(sex, ..prop.., group=1))

ggplot(enoe) +
  geom_point(aes(anios_esc, ingreso_mensual, color = num_trabajos))


ggplot(enoe) +
  stat_count(aes(sex,..prop.., group=1))


ggplot(enoe) +
  stat_summary(
    mapping = aes(sex, ingreso_mensual),
    fun.min = min,
    fun.max = max,
    fun= median)


ggplot(enoe) + 
  stat_summary(
    aes(sex, ingreso_mensual),
    fun.min = min,
    fun.max = max,
    fun=median)
ggplot(enoe) +
  stat_summary(
    aes(sex, ingreso_mensual),
    fun.min = min,
    fun.max = max,
    fun=average) 


############ barras apiladas
enoe
ggplot(enoe) +
  geom_bar(aes(sex, fill=sex))

ggplot(enoe) + 
  geom_bar(aes(sex, fill=niv_edu))

ggplot(enoe) + 
  geom_bar(aes(sex, fill=niv_edu))

ggplot(enoe, mapping = aes(sex, colour=niv_edu)) +
  geom_bar(fill = NA, position = "identity")

############ MAS UTIL
ggplot(enoe, aes(sex, fill = niv_edu)) +
  geom_bar(position = "fill")

ggplot(enoe, aes(x=factor(1), fill = ing_salarios)) + 
  geom_bar(position = "fill")

ggplot(enoe, aes(sex, fill = niv_edu)) +
  geom_bar(position = "dodge") +
  labs(title="Observaciones por nivel educativo y sexo", x="Sexo", y="observaciones", caption = "Fuente: ENOE \n Elaboracion: Propia")

?theme
?ggplot
?labs

############ jitter

ggplot(enoe) +
  geom_point(mapping = aes(anios_esc, ingreso_mensual, color = niv_edu), position = "jitter")


ggplot(enoe, aes(niv_edu, ingreso_mensual)) +
  geom_boxplot()

ggplot(enoe, aes(niv_edu, ingreso_mensual)) +
  geom_boxplot() +
  coord_flip()

ggplot(enoe, aes(niv_edu, ingreso_mensual)) +
  geom_boxplot() +
  coord_polar()

############### GGPLOT2 CON TODOS LOS USOS
ggplot(data = enoe) +
  geom_point(mapping = aes(x =anios_esc, y =ingreso_mensual, alpha= ingreso_mensual, color= tipo_empleo), position = "jitter") +
  facet_grid(tipo_empleo~sex) + 
  labs(title= "Observaciones por Tipo de empleo y sexo \n",
       x = "Años de escolaridad",
       y= "Ingreso mensual",
       alpha= "Ingreso mensual", color = "Tipo de empleo") +
  theme(axis.title = )

?theme

################ CONTINUACION COORDENADAS POLARES## OBJETO + INSTRUCCIONES

barra<-ggplot(enoe) +
  geom_bar(aes(niv_edu, fill=niv_edu),
           show.legend = F, width=1) +
  theme(aspect.ratio = 1) +
  labs(x=NULL, y = NULL)

barra + coord_flip()
barra + coord_polar()

ggplot(enoe, aes(factor(1), fill=niv_edu)) +
  geom_bar(position = "fill") +
  coord_polar(theta = "y") +
  labs(x=" ", y = " ")
?coord_polar


# ACTIVIDADES
# Elabora una gráfica de dispersión que permita observar la relación entre el salario mensual y la edad. Donde todos los puntos sean rojos

head(enoe)
ggplot(enoe) +
  geom_point(aes(edad, ingreso_mensual), color= "red")

#2. Elabora una gráfica que permita observar la relación entre el ingreso entre el salario mensual y la edad. Además que los puntos permitan identificar el nivel de escolaridad.

ggplot(enoe) + 
  geom_point(aes(edad, ingreso_mensual, color = niv_edu)) +
  labs(title = "Relacion edad e ingreso por nivel educativo \n",
       x= "EDAD", y= "Ingresos Mensuales ($)", 
       caption = "Fuente: ENOE data \n Elaboración: Propia",
       color = "Nivel Educativo")

#3. Inntenta efectuar una gráfica de puntos, donde en el eje x se observen los años de educación,en el eje y, el ingreso mensual y ademas usando la estética de alpha sobre la variable nivel de educación. ¿Cuál es el warning que emite R? ¿Porqué crees que sucede esto?

head(enoe)
ggplot(enoe, ) + 
  geom_point(aes(anios_esc, ingreso_mensual, color = niv_edu))

#rpta sucede porque el alpha y el size se utilizan para valores numericos no para valores discretos mientras que el color y el shape se utilizan para valores discretos, no numericos

#Elabora una gráfica que permita ver la relación del ingreso mensual con los años de educación diferenciada por sexo, de manera que cambie no sólo el color del punto, sino ademas la forma. ¿Qué información relevante aporta esa gráfica?

ggplot(enoe) +
  geom_point(aes(anios_esc, ingreso_mensual, color = sex, shape = sex)) +
  labs(titlte = "Relacion entre el ingreso mensual y años de educación por sexo",
       x = "Años de educación", y = "Ingreso Mensual", 
       caption = "Fuente: ENOE, Mexico \n Elaboración: Propia",
       color = "Sexo", shape = "Sexo")

#5. Construye las siguientes gráficas, analiza detenidamente las diferencias entre ellas y responde las preguntas.

ggplot(data = enoe) + 
  geom_point(mapping = aes(x =anios_esc, y =ingreso_mensual), 
             shape=3, size=5, color="red")

ggplot(data = enoe) + 
  geom_point(mapping = aes(x =anios_esc, y =ingreso_mensual), 
             shape=9, size=1, color="blue")
head(enoe)
ggplot(data = enoe) +
  geom_point(aes(anios_esc, ingreso_mensual, shape = sex , size = hrsocup, color = sex))
#cuando shape, size, color, alpha estan dentro del aes se utilizan con variables discretas o continuas, cuando estan fuera se tienen que asignar valores

#6. Elabora una gráfica que permita observar la relación la edad y el ingreso mensual, diferenciando por estados usando primero color y después shape. ¿Observas alguna incons

p<-ggplot(enoe) + 
  geom_point(aes(edad, ingreso_mensual, color = estado), size = 1) + 
  labs(title = toupper("Relacion de edad por \n ingreso mensual"), 
       subtitle = "Con distinto color por estado",
       x = "Edad", y = "Ingreso Mensual",
       caption = "Fuente: ENOE \n Elaboración: Propia")
p + theme(legend.position = "none", 
          plot.title = element_text(size = 20, face = "bold", 
                                    color = "red", hjust = 0.5),
          plot.subtitle = element_text(size = 15, face = "bold", 
                                       color = "pink", hjust = 0.5))
?theme

ggplot(enoe) + 
  geom_point(aes(edad, ingreso_mensual, shape = estado)) #error
# la inconsistencia es que no existen suficientes shapes para estado 

#7.  ¿Qué gráficas generan los siguientes códigos? ¿Que función tiene “.” dentro de código?



ggplot(data=enoe) + 
  geom_point(mapping = aes(x =anios_esc, y =ingreso_mensual)) + 
  facet_grid(sex~.)


ggplot(data=enoe)+ geom_point(mapping = aes(x =anios_esc, y =ingreso_mensual))+ 
  facet_grid(. ~ tipo_empleo)

#cuando se pone punto se elige el lado contrario de donde queremos la variable, esdecir al inicio se pone cuando se quiere la variable en el eje x, y cuando se quiere el eje y se pone al final

#8. . Replica la gráfica pera separa Jalisco, pero elimina la opción se. ¿Que función tiene se?

ggplot(enoe, aes(anios_esc, ingreso_mensual)) +
  geom_point(aes(color = tipo_empleo))+
  geom_smooth(data = filter(enoe, estado == "Jalisco"), se = F, color = "blue")



ggplot(enoe)

name_row(enoe$estado)

row.names(enoe$estado)

names(enoe$estado)
summary(enoe$estado)

boxplot(enoe)

boxplot(enoe$ingreso_mensual)

group_by(enoe, estado) %>%    #Biomasa  ~ Especies
  summarise(
    count = n(),
  )

group_by(enoe, estado) %>%
  summarise(count = n(),)

group_by(enoe, estado, anios_esc)


#9.copiar graficas

ggplot(enoe) +
  geom_point(aes(anios_esc, ingreso_mensual, color = tipo_empleo)) + 
  geom_smooth(aes(anios_esc, ingreso_mensual, linetype = sex)) +
  facet_grid(tipo_empleo~sex) +
  labs(title = toupper("Ingreso mensual de acuerdo a la educacion"),
       subtitle = "De acuerdo al sexo",
       x= "Años de educacion",
       y= "Ingreso mensual",
       caption = "Fuente: ENOE \n Elaboración: Propia",
       color = "Tipo de Empleo") +
  theme(plot.title = element_text(size = 20, color = "darkblue", face = "bold", hjust = 0.5),
        plot.subtitle = element_text(size = 17, color = "blue", face = "bold", hjust = 0.1),
        )

?theme
## 2 grafico
ggplot(enoe, aes(anios_esc, ingreso_mensual)) +
  geom_point() +
  geom_smooth(aes(linetype = sex))
## 3 grafico

ggplot(enoe, aes(anios_esc, ingreso_mensual, color = sex)) +
  geom_point() +
  geom_smooth(aes(linetype = sex))
##4 grafico

ggplot(enoe, aes(anios_esc, ingreso_mensual)) +
  geom_point(aes(color = sex)) +
  geom_smooth(aes(), color = "blue", se = F)

##5 grafico

ggplot(enoe, aes(anios_esc, ingreso_mensual)) +
  geom_point(aes(color = sex)) +
  geom_smooth(aes(linetype = sex))
?geom_smooth

##6 grafico

ggplot(enoe, aes(anios_esc, ingreso_mensual)) +
  geom_point(aes(color = sex))

###PREGUNTA 10  ¿Cuál es la diferencia entre geom_col y geom_bar?

ggplot(enoe) +
  geom_col(aes(anios_esc, ingreso_mensual))

ggplot(enoe) +
  geom_bar(mapping = aes(anios_esc))

ggplot(enoe) +
  geom_bar(aes(anios_esc))
  
# con geom_col se puede usar 2 variables, mientras que geom bar usa 1


###PREGUNTA 11Una de las geometrias en ggplot2 es geom_jitter, la cual crea el mismo efecto que position=“jitter” ¿Cómo sería el código para construir la misma gráfica usando geom_jitter en lugar de position=“jitter”?
  

ggplot(enoe) +
  geom_jitter(aes(anios_esc, ingreso_mensual))
#estos dos son equivalentes
ggplot(enoe) +
  geom_point(aes(anios_esc, ingreso_mensual), position = "jitter")










































